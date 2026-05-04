"""
Dynamic Duo Cleaning - Schedule Optimizer v7

Run locally:
    pip install -r requirements.txt
    streamlit run app.py

Host:
    Push this folder to GitHub, then deploy app.py on Streamlit Community Cloud.

Google APIs supported:
    - Geocoding API
    - Routes API / Compute Route Matrix

Keep your Google Maps API key in Streamlit secrets or environment variables.
Never commit your key to GitHub.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import hashlib
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

try:
    import pydeck as pdk
except Exception:  # pragma: no cover
    pdk = None

APP_TITLE = "Dynamic Duo Cleaning - Schedule Optimizer v7"
DEFAULT_START = "08:30"
DEFAULT_END = "17:00"
WEEKDAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_LABEL = {d: d.title() for d in WEEKDAY_ORDER}
MILES_PER_METER = 0.000621371

CITY_COORDS = {
    "minneapolis": (44.9778, -93.2650),
    "st paul": (44.9537, -93.0900),
    "saint paul": (44.9537, -93.0900),
    "apple valley": (44.7319, -93.2177),
    "burnsville": (44.7677, -93.2777),
    "eagan": (44.8041, -93.1669),
    "lakeville": (44.6497, -93.2427),
    "belle plaine": (44.6227, -93.7686),
    "eden prairie": (44.8547, -93.4708),
    "edina": (44.8897, -93.3501),
    "bloomington": (44.8408, -93.2983),
    "roseville": (45.0061, -93.1566),
    "plymouth": (45.0105, -93.4555),
    "woodbury": (44.9239, -92.9594),
    "wayzata": (44.9741, -93.5066),
    "prior lake": (44.7133, -93.4227),
    "shakopee": (44.7974, -93.5273),
    "inver grove heights": (44.8480, -93.0427),
    "farmington": (44.6402, -93.1435),
    "rosemount": (44.7394, -93.1258),
    "new prague": (44.5433, -93.5761),
    "mendota heights": (44.8836, -93.1383),
    "richfield": (44.8833, -93.2830),
    "savaged": (44.7791, -93.3363),
    "savage": (44.7791, -93.3363),
}

PALETTE = [
    [31, 119, 180],
    [255, 127, 14],
    [44, 160, 44],
    [214, 39, 40],
    [148, 103, 189],
    [140, 86, 75],
    [227, 119, 194],
    [127, 127, 127],
    [188, 189, 34],
    [23, 190, 207],
]

# -----------------------------
# Basic parsing helpers
# -----------------------------

def clean_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def key(value: Any) -> str:
    return clean_col(str(value or ""))


def truthy(value: Any) -> bool:
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"yes", "y", "true", "1", "flexible", "can shift", "can move", "locked", "lock"}


def parse_float(value: Any, default: float = 0.0) -> float:
    if pd.isna(value) or str(value).strip() == "":
        return default
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except Exception:
        return default


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return default


def parse_date(value: Any) -> Optional[date]:
    if pd.isna(value) or str(value).strip() == "":
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d-%m-%Y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except Exception:
            pass
    try:
        parsed = pd.to_datetime(raw, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None


def parse_time_to_minutes(value: Any, default: str = DEFAULT_START) -> int:
    if pd.isna(value) or str(value).strip() == "":
        value = default
    raw = str(value).strip().lower().replace(".", "")
    for fmt in ("%H:%M", "%H%M", "%I:%M %p", "%I %p", "%I%p"):
        try:
            t = datetime.strptime(raw, fmt)
            return t.hour * 60 + t.minute
        except Exception:
            pass
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", raw)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        return hour * 60 + minute
    return parse_time_to_minutes(default, DEFAULT_START)


def minutes_to_time(value: Optional[float]) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    total = int(round(float(value)))
    hour = (total // 60) % 24
    minute = total % 60
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {suffix}"


def split_list(value: Any) -> List[str]:
    if pd.isna(value) or str(value).strip() == "":
        return []
    return [x.strip() for x in re.split(r"[,;/|]+", str(value)) if x.strip()]


def split_days(value: Any) -> List[str]:
    if pd.isna(value) or str(value).strip() == "":
        return []
    raw = str(value).lower()
    replacements = {
        "mon": "monday", "tue": "tuesday", "tues": "tuesday", "wed": "wednesday",
        "thu": "thursday", "thur": "thursday", "thurs": "thursday", "fri": "friday",
        "sat": "saturday", "sun": "sunday",
    }
    tokens = re.split(r"[,/;|]+|\band\b", raw)
    found: List[str] = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        token = replacements.get(token, token)
        for day in WEEKDAY_ORDER:
            if day in token and day not in found:
                found.append(day)
    return found


def week_dates(week_start: date, horizon_weeks: int, include_weekends: bool) -> List[date]:
    monday = week_start - timedelta(days=week_start.weekday())
    all_dates = [monday + timedelta(days=i) for i in range(horizon_weeks * 7)]
    if include_weekends:
        return all_dates
    return [d for d in all_dates if d.weekday() < 5]


def date_to_day(d: date) -> str:
    return WEEKDAY_ORDER[d.weekday()]


def extract_city(address: str) -> str:
    # Good enough for route grouping. It catches common "City, MN" format.
    raw = str(address or "")
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) >= 2:
        # If first part looks like street, city is second part; otherwise first part may be city-only.
        first = parts[0].lower()
        if re.search(r"\d", first) or any(w in first for w in ["st", "ave", "road", "rd", "ln", "drive", "dr", "court", "ct"]):
            return parts[1].strip()
        return parts[0].strip()
    for city in CITY_COORDS:
        if city in raw.lower():
            return city.title()
    return raw.strip()[:40]


def priority_rank(value: Any) -> int:
    raw = str(value or "normal").strip().lower()
    if raw in {"vip", "urgent", "very high"}:
        return 0
    if raw in {"high", "important"}:
        return 1
    if raw in {"normal", "medium", ""}:
        return 2
    if raw in {"low", "flex", "flexible"}:
        return 3
    return 2


def recurrence_days(frequency: str) -> Optional[int]:
    raw = str(frequency or "").strip().lower()
    if raw in {"", "one time", "one-time", "once", "single"}:
        return None
    if "weekly" in raw and "bi" not in raw and "every 2" not in raw:
        return 7
    if "bi" in raw or "every 2" in raw or "2 week" in raw:
        return 14
    if "every 3" in raw or "3 week" in raw:
        return 21
    if "monthly" in raw or "4 week" in raw or "every 4" in raw:
        return 28
    return None

# -----------------------------
# CSV normalization
# -----------------------------

CLEANER_ALIASES = {
    "cleaner_name": "cleaner", "name": "cleaner", "employee": "cleaner", "team_member": "cleaner",
    "home_address": "base_address", "address": "base_address", "location": "base_address", "base": "base_address",
    "work_days": "available_days", "days": "available_days", "availability": "available_days",
    "max_jobs": "max_jobs_per_day", "jobs_per_day": "max_jobs_per_day",
    "max_hours": "max_hours_per_day", "hours_per_day": "max_hours_per_day",
    "hourly_rate": "hourly_cost", "rate": "hourly_cost", "pay_rate": "hourly_cost", "cost_per_hour": "hourly_cost",
    "can_work_alone": "allow_solo", "solo": "allow_solo",
}

BOOKING_ALIASES = {
    "customer": "client", "customer_name": "client", "full_name": "client", "name": "client",
    "service_address": "address", "client_address": "address", "home_address": "address", "location": "address",
    "date": "service_date", "booking_date": "service_date", "appointment_date": "service_date", "cleaning_date": "service_date",
    "day": "preferred_day", "requested_day": "preferred_day", "preferred_date": "service_date",
    "service": "cleaning_type", "service_type": "cleaning_type", "type": "cleaning_type",
    "hours": "job_hours", "estimated_hours": "job_hours", "duration": "job_hours", "job_duration": "job_hours", "person_hours": "job_hours",
    "time": "time_window", "preferred_time": "time_window", "arrival_window": "time_window",
    "can_move": "can_shift", "flexible": "can_shift", "shiftable": "can_shift",
    "price": "job_price", "quote": "job_price", "quoted_price": "job_price", "amount": "job_price", "total": "job_price",
    "assigned_cleaner": "preferred_resource", "regular_cleaner": "preferred_resource", "staff": "preferred_resource", "team": "preferred_resource",
    "preferred_cleaner": "preferred_resource", "preferred_team": "preferred_resource",
    "must_keep_cleaner": "lock_resource", "same_cleaner": "lock_resource", "locked_cleaner": "lock_resource", "cleaner_lock": "lock_resource",
    "risk": "risk_level", "overrun_risk": "risk_level", "difficulty": "difficulty_level", "buffer": "buffer_minutes",
    "extra_minutes": "buffer_minutes", "priority_level": "priority",
    "minimum_workers": "min_workers", "workers_needed": "min_workers", "team_required": "requires_team",
    "max_people": "max_workers", "maximum_workers": "max_workers",
}

AVAILABILITY_ALIASES = {
    "cleaner_name": "cleaner", "name": "cleaner", "employee": "cleaner", "team_member": "cleaner", "resource": "cleaner",
    "off_date": "date", "unavailable_date": "date", "exception_date": "date",
    "available_from": "available_start", "start": "available_start", "start_time": "available_start", "from_time": "available_start",
    "available_to": "available_end", "end": "available_end", "end_time": "available_end", "to_time": "available_end",
    "type": "status", "availability_status": "status", "unavailable_type": "status",
    "note": "reason", "notes": "reason",
}

CREW_ALIASES = {
    "crew": "resource_name", "team": "resource_name", "team_name": "resource_name", "name": "resource_name",
    "cleaners": "members", "staff": "members", "people": "members",
    "type": "team_type", "resource_type": "team_type",
    "home_address": "base_address", "address": "base_address", "base": "base_address",
    "days": "available_days", "work_days": "available_days",
    "split_after": "can_split_after_job", "split": "can_split_after_job", "car_pool": "carpool",
    "always_together": "always_together", "fixed_team": "always_together",
    "max_jobs": "max_jobs_per_day", "max_hours": "max_hours_per_day",
    "hourly_rate": "hourly_cost_override", "rate": "hourly_cost_override",
    "productivity": "productivity_multiplier", "productivity_factor": "productivity_multiplier",
}

AREA_ALIASES = {
    "area": "area_keyword", "city": "area_keyword", "location": "area_keyword",
    "best_day": "best_days", "best_day_s": "best_days", "recommended_days": "best_days",
    "avoid_day": "avoid_days", "bad_days": "avoid_days",
    "preferred_cleaner": "preferred_resources", "preferred_team": "preferred_resources", "preferred_resource": "preferred_resources",
    "note": "notes",
}



ACTUALS_ALIASES = {
    "customer": "client", "customer_name": "client", "full_name": "client", "name": "client",
    "service_address": "address", "client_address": "address", "home_address": "address", "location": "address",
    "date": "service_date", "booking_date": "service_date", "appointment_date": "service_date", "cleaning_date": "service_date",
    "estimated_hours": "estimated_hours", "estimate_hours": "estimated_hours", "estimated": "estimated_hours", "quoted_hours": "estimated_hours",
    "actual_hours": "actual_hours", "actual": "actual_hours", "clocked_hours": "actual_hours", "real_hours": "actual_hours",
    "estimated_minutes": "estimated_minutes", "actual_minutes": "actual_minutes",
    "cleaner_name": "cleaner", "employee": "cleaner", "team": "cleaner", "resource": "cleaner",
    "note": "notes", "comments": "notes",
}

def normalize_columns(df: pd.DataFrame, aliases: Dict[str, str]) -> pd.DataFrame:
    out = df.copy()
    out.columns = [clean_col(c) for c in out.columns]
    return out.rename(columns={c: aliases.get(c, c) for c in out.columns})


def prepare_cleaners(raw: pd.DataFrame) -> pd.DataFrame:
    df = normalize_columns(raw, CLEANER_ALIASES)
    if "cleaner" not in df.columns or "base_address" not in df.columns:
        raise ValueError("Cleaners CSV must include cleaner and base_address columns.")
    defaults = {
        "available_days": "Monday,Tuesday,Wednesday,Thursday,Friday",
        "max_jobs_per_day": 3,
        "max_hours_per_day": 8,
        "start_time": DEFAULT_START,
        "end_time": DEFAULT_END,
        "hourly_cost": 25,
        "allow_solo": "Yes",
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default
        df[col] = df[col].fillna(default)
    df["cleaner"] = df["cleaner"].astype(str).str.strip()
    df["cleaner_key"] = df["cleaner"].apply(key)
    df["base_address"] = df["base_address"].astype(str).str.strip()
    df["available_day_list"] = df["available_days"].apply(split_days)
    df["max_jobs_per_day"] = df["max_jobs_per_day"].apply(lambda x: max(1, parse_int(x, 3)))
    df["max_hours_per_day"] = df["max_hours_per_day"].apply(lambda x: max(1.0, parse_float(x, 8.0)))
    df["start_min"] = df["start_time"].apply(lambda x: parse_time_to_minutes(x, DEFAULT_START))
    df["end_min"] = df["end_time"].apply(lambda x: parse_time_to_minutes(x, DEFAULT_END))
    df["hourly_cost"] = df["hourly_cost"].apply(lambda x: max(0.0, parse_float(x, 25.0)))
    df["allow_solo_bool"] = df["allow_solo"].apply(truthy)
    return df


def calculate_risk_buffer_minutes(row: pd.Series) -> Tuple[int, str]:
    explicit = parse_float(row.get("buffer_minutes", ""), 0.0)
    if explicit > 0:
        return int(round(explicit)), "Manual buffer"
    risk = str(row.get("risk_level", "") or row.get("difficulty_level", "")).strip().lower()
    cleaning_type = str(row.get("cleaning_type", "") or "").strip().lower()
    buffer = 0
    source = "No buffer"
    if "very" in risk or "high" in risk or "problem" in risk or "heavy" in risk or "hard" in risk:
        buffer, source = 45, "High-risk buffer"
    elif "medium" in risk or "moderate" in risk or "normal-hard" in risk:
        buffer, source = 25, "Medium-risk buffer"
    elif "low" in risk:
        buffer, source = 10, "Low-risk buffer"
    if "move" in cleaning_type or "construction" in cleaning_type:
        if buffer < 35:
            buffer, source = 35, "Move/construction buffer"
    elif "deep" in cleaning_type:
        if buffer < 25:
            buffer, source = 25, "Deep-clean buffer"
    return int(buffer), source


def infer_time_window(row: pd.Series) -> Tuple[int, int, str, bool]:
    window = str(row.get("time_window", "Flexible") or "Flexible").strip().lower()
    default_earliest = DEFAULT_START
    default_latest = DEFAULT_END
    fixed = False
    if "morning" in window or window == "am":
        default_earliest, default_latest, label = "08:30", "12:30", "Morning"
    elif "afternoon" in window or window == "pm":
        default_earliest, default_latest, label = "12:00", "17:00", "Afternoon"
    elif "fixed" in window or "exact" in window:
        label, fixed = "Fixed", True
    else:
        label = "Flexible"
    earliest = parse_time_to_minutes(row.get("earliest_start", ""), default_earliest)
    latest_finish = parse_time_to_minutes(row.get("latest_finish", ""), default_latest)
    if latest_finish <= earliest:
        latest_finish = parse_time_to_minutes(default_latest, DEFAULT_END)
    return earliest, latest_finish, label, fixed


def prepare_bookings(raw: pd.DataFrame) -> pd.DataFrame:
    df = normalize_columns(raw, BOOKING_ALIASES)
    if "client" not in df.columns or "address" not in df.columns:
        raise ValueError("Bookings CSV must include client and address columns.")
    if "job_hours" not in df.columns:
        df["job_hours"] = 2.5
    optional_cols = [
        "service_date", "preferred_day", "flexible_days", "time_window", "cleaning_type", "can_shift",
        "notes", "earliest_start", "latest_finish", "frequency", "preferred_resource", "lock_resource",
        "job_price", "risk_level", "difficulty_level", "buffer_minutes", "priority", "min_workers", "max_workers",
        "requires_team", "recurrence_interval_weeks", "client_flexibility",
    ]
    for col in optional_cols:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")
    df["client"] = df["client"].astype(str).str.strip()
    df["client_key"] = df["client"].apply(key)
    df["address"] = df["address"].astype(str).str.strip()
    df["city"] = df["address"].apply(extract_city)
    df["job_hours"] = df["job_hours"].apply(lambda x: max(0.25, parse_float(x, 2.5)))
    df["job_mins"] = (df["job_hours"] * 60).round().astype(int)
    df["job_price_num"] = df["job_price"].apply(lambda x: max(0.0, parse_float(x, 0.0)))
    df["service_date_parsed"] = df["service_date"].apply(parse_date)
    df["preferred_day_list"] = df["preferred_day"].apply(split_days)
    df["flexible_day_list"] = df["flexible_days"].apply(split_days)
    df["can_shift_bool"] = df["can_shift"].apply(truthy)
    df["lock_resource_bool"] = df["lock_resource"].apply(truthy)
    df["preferred_resource_key"] = df["preferred_resource"].apply(key)
    df["min_workers_num"] = df["min_workers"].apply(lambda x: max(1, parse_int(x, 1)))
    df["max_workers_num"] = df["max_workers"].apply(lambda x: max(1, parse_int(x, 99)))
    df["requires_team_bool"] = df["requires_team"].apply(truthy)
    df["priority_rank"] = df["priority"].apply(priority_rank)
    risk_buffers = df.apply(calculate_risk_buffer_minutes, axis=1)
    df["buffer_mins"] = [x[0] for x in risk_buffers]
    df["buffer_source"] = [x[1] for x in risk_buffers]
    windows = df.apply(infer_time_window, axis=1)
    df["earliest_min"] = [x[0] for x in windows]
    df["latest_finish_min"] = [x[1] for x in windows]
    df["time_window_label"] = [x[2] for x in windows]
    df["fixed_time_bool"] = [x[3] for x in windows]
    return df


def prepare_availability_exceptions(raw: Optional[pd.DataFrame]) -> pd.DataFrame:
    columns = ["cleaner", "date", "day", "status", "available_start", "available_end", "reason", "date_parsed", "day_list", "available_start_min", "available_end_min", "cleaner_key"]
    if raw is None or raw.empty:
        return pd.DataFrame(columns=columns)
    df = normalize_columns(raw, AVAILABILITY_ALIASES)
    if "cleaner" not in df.columns:
        raise ValueError("Availability exceptions CSV must include a cleaner/resource column.")
    for col in ["date", "day", "status", "available_start", "available_end", "reason"]:
        if col not in df.columns:
            df[col] = ""
    df["cleaner"] = df["cleaner"].astype(str).str.strip()
    df["cleaner_key"] = df["cleaner"].apply(key)
    df["date_parsed"] = df["date"].apply(parse_date)
    df["day_list"] = df["day"].apply(split_days)
    df["available_start_min"] = df["available_start"].apply(lambda x: parse_time_to_minutes(x, DEFAULT_START))
    df["available_end_min"] = df["available_end"].apply(lambda x: parse_time_to_minutes(x, DEFAULT_END))
    df.loc[df["available_start"].isna() | (df["available_start"].astype(str).str.strip() == ""), "available_start_min"] = pd.NA
    df.loc[df["available_end"].isna() | (df["available_end"].astype(str).str.strip() == ""), "available_end_min"] = pd.NA
    return df


def prepare_crew_rules(raw: Optional[pd.DataFrame]) -> pd.DataFrame:
    cols = ["resource_name", "members", "team_type", "available_days", "base_address", "can_split_after_job", "always_together", "carpool", "max_jobs_per_day", "max_hours_per_day", "start_time", "end_time", "productivity_multiplier", "hourly_cost_override"]
    if raw is None or raw.empty:
        return pd.DataFrame(columns=cols)
    df = normalize_columns(raw, CREW_ALIASES)
    if "resource_name" not in df.columns or "members" not in df.columns:
        raise ValueError("Crew rules CSV must include resource_name and members columns.")
    defaults = {
        "team_type": "Optional", "available_days": "Monday,Tuesday,Wednesday,Thursday,Friday", "base_address": "",
        "can_split_after_job": "Yes", "always_together": "No", "carpool": "Yes", "max_jobs_per_day": 3,
        "max_hours_per_day": 8, "start_time": DEFAULT_START, "end_time": DEFAULT_END, "productivity_multiplier": "", "hourly_cost_override": "",
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default
        df[col] = df[col].fillna(default)
    df["resource_name"] = df["resource_name"].astype(str).str.strip()
    df["resource_key"] = df["resource_name"].apply(key)
    df["member_list"] = df["members"].apply(lambda x: [m.strip() for m in re.split(r"[,;/|+]+|\band\b", str(x)) if m.strip()])
    df["member_keys"] = df["member_list"].apply(lambda xs: [key(x) for x in xs])
    df["available_day_list"] = df["available_days"].apply(split_days)
    df["can_split_bool"] = df["can_split_after_job"].apply(truthy)
    df["always_together_bool"] = df["always_together"].apply(truthy)
    df["carpool_bool"] = df["carpool"].apply(truthy)
    df["max_jobs_per_day"] = df["max_jobs_per_day"].apply(lambda x: max(1, parse_int(x, 3)))
    df["max_hours_per_day"] = df["max_hours_per_day"].apply(lambda x: max(1.0, parse_float(x, 8.0)))
    df["start_min"] = df["start_time"].apply(lambda x: parse_time_to_minutes(x, DEFAULT_START))
    df["end_min"] = df["end_time"].apply(lambda x: parse_time_to_minutes(x, DEFAULT_END))
    df["productivity_multiplier_num"] = df["productivity_multiplier"].apply(lambda x: parse_float(x, 0.0))
    df["hourly_cost_override_num"] = df["hourly_cost_override"].apply(lambda x: parse_float(x, -1.0))
    return df


def prepare_area_memory(raw: Optional[pd.DataFrame]) -> pd.DataFrame:
    cols = ["area_keyword", "best_days", "avoid_days", "preferred_resources", "notes", "best_day_list", "avoid_day_list", "preferred_resource_keys"]
    if raw is None or raw.empty:
        return pd.DataFrame(columns=cols)
    df = normalize_columns(raw, AREA_ALIASES)
    if "area_keyword" not in df.columns:
        raise ValueError("Area memory CSV must include area_keyword/city column.")
    for col in ["best_days", "avoid_days", "preferred_resources", "notes"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")
    df["area_keyword"] = df["area_keyword"].astype(str).str.strip()
    df["area_key"] = df["area_keyword"].apply(key)
    df["best_day_list"] = df["best_days"].apply(split_days)
    df["avoid_day_list"] = df["avoid_days"].apply(split_days)
    df["preferred_resource_keys"] = df["preferred_resources"].apply(lambda x: [key(v) for v in split_list(x)])
    return df


def prepare_actuals(raw: Optional[pd.DataFrame]) -> pd.DataFrame:
    cols = ["client", "address", "service_date", "cleaner", "estimated_hours", "actual_hours", "estimated_minutes", "actual_minutes", "notes", "client_key", "address_key", "city", "service_date_parsed", "estimated_mins_num", "actual_mins_num", "variance_mins", "overrun_pct"]
    if raw is None or raw.empty:
        return pd.DataFrame(columns=cols)
    df = normalize_columns(raw, ACTUALS_ALIASES)
    if "client" not in df.columns:
        raise ValueError("Actual vs estimated CSV must include a client/customer column.")
    for col in ["address", "service_date", "cleaner", "estimated_hours", "actual_hours", "estimated_minutes", "actual_minutes", "notes"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")
    df["client"] = df["client"].astype(str).str.strip()
    df["client_key"] = df["client"].apply(key)
    df["address"] = df["address"].astype(str).str.strip()
    df["address_key"] = df["address"].apply(key)
    df["city"] = df["address"].apply(extract_city)
    df["service_date_parsed"] = df["service_date"].apply(parse_date)

    def mins(row: pd.Series, hour_col: str, min_col: str) -> float:
        minute_val = parse_float(row.get(min_col, ""), 0.0)
        if minute_val > 0:
            return minute_val
        hour_val = parse_float(row.get(hour_col, ""), 0.0)
        return hour_val * 60

    df["estimated_mins_num"] = df.apply(lambda r: mins(r, "estimated_hours", "estimated_minutes"), axis=1)
    df["actual_mins_num"] = df.apply(lambda r: mins(r, "actual_hours", "actual_minutes"), axis=1)
    df = df[(df["estimated_mins_num"] > 0) & (df["actual_mins_num"] > 0)].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["variance_mins"] = df["actual_mins_num"] - df["estimated_mins_num"]
    df["overrun_pct"] = ((df["actual_mins_num"] / df["estimated_mins_num"]) - 1.0).round(3)
    return df


def apply_actual_time_learning(bookings: pd.DataFrame, actuals: pd.DataFrame, max_extra_buffer: int = 60) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Adjust future estimates using previous actual-vs-estimated results.

    Client match gets the strongest adjustment. City match is used only as a soft fallback.
    This keeps the first version simple but useful: it learns that specific homes, or some areas,
    tend to run longer than the original estimate.
    """
    if actuals is None or actuals.empty or bookings.empty:
        out = bookings.copy()
        if "time_learning_note" not in out.columns:
            out["time_learning_note"] = ""
        return out, pd.DataFrame()

    out = bookings.copy()
    out["time_learning_note"] = ""
    learning_rows: List[Dict[str, Any]] = []

    client_stats = actuals.groupby("client_key").agg(
        samples=("client_key", "size"),
        avg_variance_mins=("variance_mins", "mean"),
        avg_overrun_pct=("overrun_pct", "mean"),
        avg_actual_mins=("actual_mins_num", "mean"),
    ).reset_index()
    client_lookup = {r["client_key"]: r for _, r in client_stats.iterrows()}

    city_stats = actuals.groupby("city").agg(
        samples=("city", "size"),
        avg_variance_mins=("variance_mins", "mean"),
        avg_overrun_pct=("overrun_pct", "mean"),
    ).reset_index()
    city_lookup = {str(r["city"]).lower(): r for _, r in city_stats.iterrows()}

    for idx, row in out.iterrows():
        client_key = row.get("client_key", key(row.get("client", "")))
        city = str(row.get("city", "")).lower()
        applied = 0
        source = ""
        samples = 0
        overrun_pct = 0.0
        if client_key in client_lookup:
            stat = client_lookup[client_key]
            samples = int(stat["samples"])
            overrun_pct = float(stat["avg_overrun_pct"])
            if samples >= 1 and overrun_pct > 0.08:
                applied = int(min(max_extra_buffer, max(10, round(float(stat["avg_variance_mins"]) / 5) * 5)))
                source = f"client history: {samples} sample(s), avg overrun {overrun_pct*100:.0f}%"
        elif city in city_lookup:
            stat = city_lookup[city]
            samples = int(stat["samples"])
            overrun_pct = float(stat["avg_overrun_pct"])
            if samples >= 2 and overrun_pct > 0.12:
                applied = int(min(max_extra_buffer, max(5, round(float(stat["avg_variance_mins"]) / 10) * 5)))
                source = f"area history: {samples} sample(s), avg overrun {overrun_pct*100:.0f}%"

        if applied > 0:
            old_buffer = int(out.at[idx, "buffer_mins"]) if "buffer_mins" in out.columns and not pd.isna(out.at[idx, "buffer_mins"]) else 0
            out.at[idx, "buffer_mins"] = max(old_buffer, applied)
            old_source = str(out.at[idx, "buffer_source"] or "") if "buffer_source" in out.columns else ""
            out.at[idx, "buffer_source"] = "; ".join([x for x in [old_source, "Actual-time learning"] if x and x != "No buffer"])
            out.at[idx, "time_learning_note"] = source
        learning_rows.append({
            "client": row.get("client"),
            "city": row.get("city"),
            "applied_extra_buffer_mins": applied,
            "learning_source": source or "No history adjustment",
            "history_samples": samples,
            "avg_overrun_pct": round(overrun_pct, 3),
        })
    return out, pd.DataFrame(learning_rows)

# -----------------------------
# Location and routing helpers
# -----------------------------

def get_secret_value(name: str) -> str:
    try:
        val = st.secrets.get(name, "")
        if val:
            return str(val)
    except Exception:
        pass
    return os.environ.get(name, "")


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24 * 14)
def geocode_address(address: str, api_key: str) -> Tuple[float, float, str, str]:
    if not api_key:
        lat, lng = approximate_address_coords(address)
        return lat, lng, address, "Approximate/no API"
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    resp = requests.get(url, params={"address": address, "key": api_key}, timeout=25)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "OK" or not data.get("results"):
        lat, lng = approximate_address_coords(address)
        return lat, lng, address, f"Approximate/geocode failed: {data.get('status')}"
    result = data["results"][0]
    loc = result["geometry"]["location"]
    return float(loc["lat"]), float(loc["lng"]), result.get("formatted_address", address), "Google"


def approximate_address_coords(address: str) -> Tuple[float, float]:
    raw = str(address or "").lower()
    for city, coords in CITY_COORDS.items():
        if city in raw:
            return coords
    # Stable fake point around Twin Cities so the demo still runs without an API key.
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()
    lat_offset = (int(digest[:4], 16) / 65535 - 0.5) * 0.8
    lng_offset = (int(digest[4:8], 16) / 65535 - 0.5) * 1.0
    return 44.9 + lat_offset, -93.25 + lng_offset


def haversine_miles(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    x = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 3958.8 * 2 * math.atan2(math.sqrt(x), math.sqrt(1 - x))


def duration_to_minutes(value: Any) -> float:
    if not value:
        return math.inf
    m = re.match(r"([0-9.]+)s", str(value))
    if not m:
        return math.inf
    return float(m.group(1)) / 60.0


@st.cache_data(show_spinner=False, ttl=60 * 60 * 8)
def compute_route_matrix(points: List[Dict[str, Any]], api_key: str, use_google: bool, chunk_size: int = 20) -> Tuple[List[List[float]], List[List[float]], str]:
    n = len(points)
    if not use_google or not api_key:
        miles = [[0.0] * n for _ in range(n)]
        minutes = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                m = haversine_miles((points[i]["lat"], points[i]["lng"]), (points[j]["lat"], points[j]["lng"]))
                # Road factor + average city speed approximation.
                road_miles = m * 1.22
                miles[i][j] = road_miles
                minutes[i][j] = max(3, road_miles / 28 * 60)
        return miles, minutes, "Approximate fallback"

    miles = [[0.0 if i == j else math.inf for j in range(n)] for i in range(n)]
    minutes = [[0.0 if i == j else math.inf for j in range(n)] for i in range(n)]
    url = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "originIndex,destinationIndex,duration,distanceMeters,condition,status",
    }

    def make_waypoint(p: Dict[str, Any]) -> Dict[str, Any]:
        return {"waypoint": {"location": {"latLng": {"latitude": float(p["lat"]), "longitude": float(p["lng"])}}}}

    try:
        for oi in range(0, n, chunk_size):
            o_chunk = points[oi: oi + chunk_size]
            for di in range(0, n, chunk_size):
                d_chunk = points[di: di + chunk_size]
                body = {
                    "origins": [make_waypoint(p) for p in o_chunk],
                    "destinations": [make_waypoint(p) for p in d_chunk],
                    "travelMode": "DRIVE",
                    "routingPreference": "TRAFFIC_UNAWARE",
                    "units": "IMPERIAL",
                }
                resp = requests.post(url, headers=headers, json=body, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                for entry in data:
                    src = oi + int(entry.get("originIndex", 0))
                    dst = di + int(entry.get("destinationIndex", 0))
                    if src == dst:
                        continue
                    if entry.get("condition") == "ROUTE_EXISTS" or entry.get("distanceMeters") is not None:
                        miles[src][dst] = float(entry.get("distanceMeters", 0)) * MILES_PER_METER
                        minutes[src][dst] = duration_to_minutes(entry.get("duration"))
        return miles, minutes, "Google Routes API"
    except Exception as exc:
        st.warning(f"Google routing failed, using approximate fallback instead. Details: {exc}")
        return compute_route_matrix(points, "", False)

# -----------------------------
# Resource, recurrence, and optimization
# -----------------------------

def build_resources(cleaners: pd.DataFrame, crews: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Dict[str, Any]]]:
    cleaner_lookup = {r["cleaner_key"]: r.to_dict() for _, r in cleaners.iterrows()}
    fixed_members = set()
    resources: List[Dict[str, Any]] = []

    for _, crew in crews.iterrows():
        member_keys = crew["member_keys"]
        member_names = crew["member_list"]
        if crew["always_together_bool"]:
            fixed_members.update(member_keys)
        hourly_sum = sum(float(cleaner_lookup.get(m, {}).get("hourly_cost", 25.0)) for m in member_keys) or 25.0 * len(member_keys)
        base_address = str(crew.get("base_address", "") or "").strip()
        if not base_address and member_keys:
            base_address = str(cleaner_lookup.get(member_keys[0], {}).get("base_address", ""))
        max_jobs = parse_int(crew.get("max_jobs_per_day", 3), 3)
        max_hours = parse_float(crew.get("max_hours_per_day", 8), 8)
        start_min = parse_time_to_minutes(crew.get("start_time", DEFAULT_START), DEFAULT_START)
        end_min = parse_time_to_minutes(crew.get("end_time", DEFAULT_END), DEFAULT_END)
        prod = float(crew.get("productivity_multiplier_num", 0.0) or 0.0)
        if prod <= 0:
            prod = max(1.0, len(member_keys))
        hourly_override = float(crew.get("hourly_cost_override_num", -1.0) or -1.0)
        resources.append({
            "resource": crew["resource_name"],
            "resource_key": crew["resource_key"],
            "resource_type": "Crew",
            "members": ", ".join(member_names),
            "member_keys": member_keys,
            "member_count": max(1, len(member_keys)),
            "base_address": base_address,
            "available_day_list": crew["available_day_list"],
            "max_jobs_per_day": max_jobs,
            "max_hours_per_day": max_hours,
            "start_min": start_min,
            "end_min": end_min,
            "hourly_cost": hourly_override if hourly_override >= 0 else hourly_sum,
            "productivity_multiplier": prod,
            "can_split_after_job": crew["can_split_bool"],
            "always_together": crew["always_together_bool"],
            "carpool": crew["carpool_bool"],
        })

    for _, cleaner in cleaners.iterrows():
        ck = cleaner["cleaner_key"]
        if ck in fixed_members:
            continue
        if not bool(cleaner.get("allow_solo_bool", True)):
            continue
        resources.append({
            "resource": cleaner["cleaner"],
            "resource_key": ck,
            "resource_type": "Solo",
            "members": cleaner["cleaner"],
            "member_keys": [ck],
            "member_count": 1,
            "base_address": cleaner["base_address"],
            "available_day_list": cleaner["available_day_list"],
            "max_jobs_per_day": cleaner["max_jobs_per_day"],
            "max_hours_per_day": cleaner["max_hours_per_day"],
            "start_min": cleaner["start_min"],
            "end_min": cleaner["end_min"],
            "hourly_cost": cleaner["hourly_cost"],
            "productivity_multiplier": 1.0,
            "can_split_after_job": True,
            "always_together": False,
            "carpool": False,
        })

    res_df = pd.DataFrame(resources)
    resource_lookup = {r["resource_key"]: r for r in resources}
    return res_df, resource_lookup


def expand_recurring_bookings(bookings: pd.DataFrame, dates: List[date]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if not dates:
        return pd.DataFrame()
    start, end = min(dates), max(dates)
    allowed_set = set(dates)
    for idx, row in bookings.iterrows():
        base = row.to_dict()
        anchor = row.get("service_date_parsed")
        rec = recurrence_days(row.get("frequency", ""))
        interval_weeks = parse_int(row.get("recurrence_interval_weeks", ""), 0)
        if interval_weeks > 0:
            rec = interval_weeks * 7
        generated: List[date] = []
        if anchor:
            if rec:
                d = anchor
                # move near horizon without looping forever
                while d < start:
                    d += timedelta(days=rec)
                while d <= end:
                    if d in allowed_set:
                        generated.append(d)
                    d += timedelta(days=rec)
            else:
                if anchor in allowed_set:
                    generated.append(anchor)
                elif row.get("can_shift_bool"):
                    # fixed date outside selected week is skipped.
                    pass
        else:
            # No fixed date: use preferred/flexible weekdays in the first selected week only.
            possible_days = row.get("preferred_day_list") or row.get("flexible_day_list") or []
            if not possible_days:
                possible_days = WEEKDAY_ORDER[:5]
            for d in dates:
                if date_to_day(d) in possible_days:
                    generated.append(d)
                    break
        if not generated and not anchor:
            # last resort: put it on the first planning day for manual review.
            generated.append(start)
        for seq, d in enumerate(generated):
            inst = dict(base)
            inst["booking_index"] = idx
            inst["instance_id"] = f"{key(row.get('client'))}_{d.isoformat()}_{seq}"
            inst["candidate_anchor_date"] = d
            inst["candidate_anchor_day"] = date_to_day(d)
            rows.append(inst)
    return pd.DataFrame(rows)


def candidate_dates_for_booking(row: pd.Series, dates: List[date]) -> List[date]:
    allowed = set(dates)
    anchor = row.get("candidate_anchor_date")
    if isinstance(anchor, pd.Timestamp):
        anchor = anchor.date()
    out: List[date] = []
    if anchor in allowed:
        out.append(anchor)
    fixed = bool(row.get("fixed_time_bool")) or not bool(row.get("can_shift_bool"))
    if fixed and out:
        return out
    flexible_days = row.get("flexible_day_list") or []
    preferred_days = row.get("preferred_day_list") or []
    days = flexible_days or preferred_days
    if bool(row.get("can_shift_bool")) and days:
        for d in dates:
            if date_to_day(d) in days and d not in out:
                out.append(d)
    elif bool(row.get("can_shift_bool")):
        # If flexible but no day list, allow nearby dates in the same selected horizon.
        for d in dates:
            if len(out) >= 5:
                break
            if d not in out:
                out.append(d)
    return out or ([dates[0]] if dates else [])


def availability_for_resource(resource: Dict[str, Any], d: date, exceptions: pd.DataFrame) -> Tuple[bool, int, int, str]:
    day = date_to_day(d)
    if resource.get("available_day_list") and day not in resource["available_day_list"]:
        return False, resource["start_min"], resource["end_min"], "Not normally available that day"
    start, end = int(resource["start_min"]), int(resource["end_min"])
    notes: List[str] = []
    keys_to_check = [resource["resource_key"]] + list(resource.get("member_keys", []))
    if exceptions is not None and not exceptions.empty:
        for _, ex in exceptions.iterrows():
            if ex.get("cleaner_key") not in keys_to_check:
                continue
            applies = False
            if ex.get("date_parsed") == d:
                applies = True
            elif ex.get("day_list") and day in ex.get("day_list"):
                applies = True
            if not applies:
                continue
            status = str(ex.get("status", "") or "").lower()
            reason = str(ex.get("reason", "") or "").strip()
            if any(x in status for x in ["off", "unavailable", "emergency", "sick", "pto"]):
                return False, start, end, reason or "Unavailable exception"
            if not pd.isna(ex.get("available_start_min")):
                start = max(start, int(ex.get("available_start_min")))
                notes.append(reason or "Start override")
            if not pd.isna(ex.get("available_end_min")):
                end = min(end, int(ex.get("available_end_min")))
                notes.append(reason or "End override")
    if end <= start:
        return False, start, end, "No available hours after exceptions"
    return True, start, end, "; ".join([n for n in notes if n])


def interval_overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


def member_conflict(member_events: Dict[Tuple[str, date], List[Tuple[int, int, str]]], member_keys: List[str], d: date, start: int, end: int) -> Optional[str]:
    for m in member_keys:
        for s, e, label in member_events.get((m, d), []):
            if interval_overlaps(start, end, s, e):
                return f"{m} overlaps with {label}"
    return None


def actual_job_minutes(row: pd.Series, resource: Dict[str, Any], hours_are_person_hours: bool) -> int:
    base = int(row.get("job_mins", 150))
    buffer_mins = int(row.get("buffer_mins", 0))
    if hours_are_person_hours:
        effective_workers = max(1.0, float(resource.get("productivity_multiplier", resource.get("member_count", 1))))
        return int(math.ceil(base / effective_workers + buffer_mins))
    return int(base + buffer_mins)


def match_area_memory(row: pd.Series, area_memory: pd.DataFrame) -> Optional[pd.Series]:
    if area_memory is None or area_memory.empty:
        return None
    hay = f"{row.get('address','')} {row.get('city','')}".lower()
    for _, a in area_memory.iterrows():
        needle = str(a.get("area_keyword", "")).lower().strip()
        if needle and needle in hay:
            return a
    return None


def area_memory_adjustment(row: pd.Series, resource: Dict[str, Any], d: date, area_memory: pd.DataFrame) -> Tuple[float, str]:
    match = match_area_memory(row, area_memory)
    if match is None:
        return 0.0, ""
    day = date_to_day(d)
    adj = 0.0
    reasons: List[str] = []
    if day in (match.get("best_day_list") or []):
        adj -= 12
        reasons.append(f"area memory says {DAY_LABEL[day]} works well")
    if day in (match.get("avoid_day_list") or []):
        adj += 20
        reasons.append(f"area memory says avoid {DAY_LABEL[day]}")
    pref = match.get("preferred_resource_keys") or []
    resource_keys = [resource["resource_key"]] + list(resource.get("member_keys", []))
    if any(p in resource_keys for p in pref):
        adj -= 10
        reasons.append("preferred resource for this area")
    return adj, "; ".join(reasons)


def evaluate_candidate(row: pd.Series, resource: Dict[str, Any], d: date, schedules: Dict[Tuple[str, date], List[Dict[str, Any]]], member_events: Dict[Tuple[str, date], List[Tuple[int, int, str]]], exceptions: pd.DataFrame, miles: List[List[float]], minutes: List[List[float]], point_idx: Dict[str, int], area_memory: pd.DataFrame, hours_are_person_hours: bool, mileage_cost: float, travel_hour_cost: float) -> Tuple[bool, Dict[str, Any]]:
    ok, avail_start, avail_end, avail_note = availability_for_resource(resource, d, exceptions)
    if not ok:
        return False, {"reason": avail_note}

    if bool(row.get("requires_team_bool")) and int(resource.get("member_count", 1)) < 2:
        return False, {"reason": "Requires a team/crew"}
    if int(resource.get("member_count", 1)) < int(row.get("min_workers_num", 1)):
        return False, {"reason": "Not enough workers"}
    if int(resource.get("member_count", 1)) > int(row.get("max_workers_num", 99)):
        return False, {"reason": "Too many workers for this job"}

    pref_key = row.get("preferred_resource_key", "")
    if bool(row.get("lock_resource_bool")) and pref_key:
        allowed_keys = [resource["resource_key"]] + list(resource.get("member_keys", []))
        if pref_key not in allowed_keys:
            return False, {"reason": "Locked to another cleaner/team"}

    route = schedules.get((resource["resource_key"], d), [])
    current_jobs = len(route)
    if current_jobs >= int(resource.get("max_jobs_per_day", 3)):
        return False, {"reason": "Max jobs reached"}

    resource_base_key = f"base::{resource['resource_key']}"
    job_key = f"job::{row['instance_id']}"
    last_key = route[-1]["point_key"] if route else resource_base_key
    travel_miles = miles[point_idx[last_key]][point_idx[job_key]]
    travel_minutes = minutes[point_idx[last_key]][point_idx[job_key]]
    if math.isinf(travel_miles) or math.isinf(travel_minutes):
        return False, {"reason": "No route data"}
    prev_end = route[-1]["end_min"] if route else avail_start
    arrival = prev_end + int(math.ceil(travel_minutes))
    start = max(int(row.get("earliest_min", avail_start)), arrival, avail_start)
    duration = actual_job_minutes(row, resource, hours_are_person_hours)
    end = start + duration
    if end > int(row.get("latest_finish_min", avail_end)):
        return False, {"reason": "Does not fit client time window"}
    if end > avail_end:
        return False, {"reason": "Does not fit cleaner hours"}
    total_work_mins = sum(int(r.get("duration_mins", 0)) for r in route) + duration
    if total_work_mins / 60 > float(resource.get("max_hours_per_day", 8)):
        return False, {"reason": "Max route hours exceeded"}
    conflict = member_conflict(member_events, list(resource.get("member_keys", [])), d, start, end)
    if conflict:
        return False, {"reason": conflict}

    area_adj, area_reason = area_memory_adjustment(row, resource, d, area_memory)
    pref_bonus = 0.0
    if pref_key and not bool(row.get("lock_resource_bool")):
        allowed_keys = [resource["resource_key"]] + list(resource.get("member_keys", []))
        if pref_key in allowed_keys:
            pref_bonus -= 12
    shift_penalty = 0 if d == row.get("candidate_anchor_date") else 18
    priority_penalty = float(row.get("priority_rank", 2)) * 1.0
    team_penalty = 0.0
    if resource.get("resource_type") == "Crew" and not bool(row.get("requires_team_bool")) and int(row.get("min_workers_num", 1)) <= 1:
        # Don't use crews on tiny jobs unless it still helps route/profit.
        team_penalty += 4
    score = travel_miles + (travel_minutes / 8) + shift_penalty + priority_penalty + pref_bonus + area_adj + team_penalty
    if str(row.get("time_window_label", "")).lower() in {"fixed", "morning"}:
        score -= 2

    labor_cost = (duration / 60) * float(resource.get("hourly_cost", 0.0))
    travel_cost = travel_miles * mileage_cost + (travel_minutes / 60) * travel_hour_cost
    profit = float(row.get("job_price_num", 0.0)) - labor_cost - travel_cost if float(row.get("job_price_num", 0.0)) > 0 else math.nan

    return True, {
        "resource": resource["resource"],
        "resource_key": resource["resource_key"],
        "resource_type": resource["resource_type"],
        "members": resource["members"],
        "member_keys": resource["member_keys"],
        "date": d,
        "day": DAY_LABEL[date_to_day(d)],
        "client": row.get("client"),
        "address": row.get("address"),
        "city": row.get("city"),
        "cleaning_type": row.get("cleaning_type"),
        "priority": row.get("priority") or "Normal",
        "time_window": row.get("time_window_label"),
        "start_min": start,
        "end_min": end,
        "start": minutes_to_time(start),
        "end": minutes_to_time(end),
        "duration_mins": duration,
        "duration_hours": round(duration / 60, 2),
        "travel_miles": round(travel_miles, 1),
        "travel_minutes": round(travel_minutes, 0),
        "job_price": float(row.get("job_price_num", 0.0)),
        "labor_cost": round(labor_cost, 2),
        "travel_cost": round(travel_cost, 2),
        "profit_score": round(profit, 2) if not math.isnan(profit) else "",
        "buffer_mins": int(row.get("buffer_mins", 0)),
        "buffer_source": row.get("buffer_source", ""),
        "time_learning_note": row.get("time_learning_note", ""),
        "risk_level": row.get("risk_level", "") or row.get("difficulty_level", ""),
        "score": round(score, 2),
        "score_notes": "; ".join([x for x in [avail_note, area_reason] if x]),
        "point_key": job_key,
        "instance_id": row.get("instance_id"),
        "can_shift": bool(row.get("can_shift_bool")),
        "anchor_date": row.get("candidate_anchor_date"),
    }


def optimize_schedule(bookings_inst: pd.DataFrame, resources: pd.DataFrame, dates: List[date], exceptions: pd.DataFrame, miles: List[List[float]], minutes: List[List[float]], point_idx: Dict[str, int], area_memory: pd.DataFrame, hours_are_person_hours: bool, mileage_cost: float, travel_hour_cost: float) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[Tuple[str, date], List[Dict[str, Any]]], Dict[Tuple[str, date], List[Tuple[int, int, str]]]]:
    schedules: Dict[Tuple[str, date], List[Dict[str, Any]]] = {}
    member_events: Dict[Tuple[str, date], List[Tuple[int, int, str]]] = {}
    assigned: List[Dict[str, Any]] = []
    unassigned: List[Dict[str, Any]] = []

    if bookings_inst.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), schedules, member_events

    sort_df = bookings_inst.copy()
    sort_df["fixed_sort"] = sort_df["fixed_time_bool"].apply(lambda x: 0 if x else 1)
    sort_df["lock_sort"] = sort_df["lock_resource_bool"].apply(lambda x: 0 if x else 1)
    sort_df = sort_df.sort_values(["priority_rank", "fixed_sort", "lock_sort", "earliest_min", "job_mins"], ascending=[True, True, True, True, False])

    for _, row in sort_df.iterrows():
        best: Optional[Dict[str, Any]] = None
        best_fail_reasons: List[str] = []
        cdates = candidate_dates_for_booking(row, dates)
        for d in cdates:
            for _, res_row in resources.iterrows():
                resource = res_row.to_dict()
                ok, cand = evaluate_candidate(row, resource, d, schedules, member_events, exceptions, miles, minutes, point_idx, area_memory, hours_are_person_hours, mileage_cost, travel_hour_cost)
                if ok:
                    if best is None or float(cand["score"]) < float(best["score"]):
                        best = cand
                else:
                    reason = cand.get("reason", "Not feasible")
                    if reason not in best_fail_reasons and len(best_fail_reasons) < 5:
                        best_fail_reasons.append(reason)
        if best:
            schedules.setdefault((best["resource_key"], best["date"]), []).append(best)
            schedules[(best["resource_key"], best["date"])].sort(key=lambda x: x["start_min"])
            for m in best["member_keys"]:
                member_events.setdefault((m, best["date"]), []).append((best["start_min"], best["end_min"], str(best["client"])))
            assigned.append(best)
        else:
            unassigned.append({
                "client": row.get("client"),
                "address": row.get("address"),
                "city": row.get("city"),
                "anchor_date": row.get("candidate_anchor_date"),
                "priority": row.get("priority") or "Normal",
                "reason": "; ".join(best_fail_reasons) or "No feasible cleaner/team/date found",
                "suggestion": "Use Emergency Rescue Mode: reassign, move a low-priority job, or offer a flexible-date discount.",
                "instance_id": row.get("instance_id"),
            })

    schedule_df = pd.DataFrame(assigned)
    if not schedule_df.empty:
        schedule_df = schedule_df.sort_values(["date", "resource", "start_min"])
    unassigned_df = pd.DataFrame(unassigned)
    alerts_df = build_alerts(schedule_df, resources, miles, minutes, point_idx, mileage_cost)
    return schedule_df, unassigned_df, alerts_df, schedules, member_events


def build_alerts(schedule: pd.DataFrame, resources: pd.DataFrame, miles: List[List[float]], minutes: List[List[float]], point_idx: Dict[str, int], mileage_cost: float) -> pd.DataFrame:
    alerts: List[Dict[str, Any]] = []
    if schedule.empty:
        return pd.DataFrame()

    # If a cleaner is used solo and also as part of a crew on the same day,
    # the system prevents overlap, but the exact meet-up/join travel still
    # deserves human review.
    member_day_resources: Dict[Tuple[str, Any], set] = {}
    for _, rr in schedule.iterrows():
        members = rr.get("member_keys", [])
        if not isinstance(members, list):
            members = [m.strip() for m in str(members).split(",") if m.strip()]
        for m in members:
            member_day_resources.setdefault((str(m), rr.get("date")), set()).add(str(rr.get("resource")))
    for (member, d0), resources_used in member_day_resources.items():
        if len(resources_used) > 1:
            alerts.append({"type": "Split/recombine review", "severity": "Medium", "date": d0, "resource": ", ".join(sorted(resources_used)), "client": "", "message": f"{member} is used across multiple solo/team routes on the same day.", "advice": "Check exact meet-up point and travel time before confirming this split/recombine plan."})

    for (resource, d), group in schedule.groupby(["resource_key", "date"]):
        group = group.sort_values("start_min")
        rows = group.to_dict("records")
        for i, r in enumerate(rows):
            if float(r.get("travel_miles", 0)) >= 25:
                alerts.append({"type": "Bad route warning", "severity": "High", "date": d, "resource": r["resource"], "client": r["client"], "message": f"Long travel leg: {r['travel_miles']} miles before this job.", "advice": "Try moving this client to a better cluster day, charge a travel premium, or assign a closer cleaner."})
            profit = r.get("profit_score", "")
            try:
                if profit != "" and float(profit) < 40:
                    alerts.append({"type": "Price adjustment", "severity": "Medium", "date": d, "resource": r["resource"], "client": r["client"], "message": f"Low estimated profit: ${float(profit):.0f}.", "advice": "Raise quote, add travel fee, or move to a nearby cluster day before confirming."})
            except Exception:
                pass
            if i + 1 < len(rows):
                nxt = rows[i + 1]
                from_key = r["point_key"]
                to_key = nxt["point_key"]
                drive = minutes[point_idx[from_key]][point_idx[to_key]] if from_key in point_idx and to_key in point_idx else 0
                available_gap = nxt["start_min"] - r["end_min"] - drive
                risk = str(r.get("risk_level", "") or "").lower()
                needed_gap = 30 if ("high" in risk or "hard" in risk or int(r.get("buffer_mins", 0)) >= 30) else 15
                if available_gap < needed_gap:
                    alerts.append({"type": "Late-running alert", "severity": "High" if needed_gap >= 30 else "Medium", "date": d, "resource": r["resource"], "client": r["client"], "message": f"Only about {max(0, round(available_gap))} min cushion before {nxt['client']} after drive time.", "advice": "Add buffer, move the second job later, or assign a separate cleaner/team."})
        total_miles = group["travel_miles"].astype(float).sum()
        if len(group) == 1 and total_miles >= 20:
            r = rows[0]
            alerts.append({"type": "One-job far route", "severity": "Medium", "date": d, "resource": r["resource"], "client": r["client"], "message": f"Only one job on this route with {total_miles:.1f} miles before arrival.", "advice": "Try adding another nearby client, moving this to a cluster day, or charging more."})
    return pd.DataFrame(alerts)


def make_points(resources: pd.DataFrame, bookings_inst: pd.DataFrame, api_key: str, use_google: bool) -> Tuple[List[Dict[str, Any]], Dict[str, int], pd.DataFrame]:
    rows: List[Dict[str, Any]] = []
    for _, r in resources.iterrows():
        addr = str(r["base_address"])
        lat, lng, formatted, source = geocode_address(addr, api_key if use_google else "")
        rows.append({"point_key": f"base::{r['resource_key']}", "name": f"{r['resource']} base", "address": addr, "lat": lat, "lng": lng, "kind": "Base", "source": source, "resource_key": r["resource_key"]})
    # Unique job instance points.
    for _, b in bookings_inst.iterrows():
        addr = str(b["address"])
        lat, lng, formatted, source = geocode_address(addr, api_key if use_google else "")
        rows.append({"point_key": f"job::{b['instance_id']}", "name": b["client"], "address": addr, "lat": lat, "lng": lng, "kind": "Job", "source": source, "resource_key": ""})
    points = rows
    point_idx = {p["point_key"]: i for i, p in enumerate(points)}
    return points, point_idx, pd.DataFrame(rows)

# -----------------------------
# Suggestions, text messages, maps, exports
# -----------------------------

def price_adjustment_suggestions(schedule: pd.DataFrame, min_profit: float, long_drive_miles: float) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if schedule.empty:
        return pd.DataFrame()
    for _, r in schedule.iterrows():
        suggestions: List[str] = []
        added = 0.0
        try:
            profit = float(r.get("profit_score", math.nan))
        except Exception:
            profit = math.nan
        miles_val = float(r.get("travel_miles", 0.0))
        if not math.isnan(profit) and profit < min_profit:
            added = max(15.0, min_profit - profit)
            suggestions.append(f"Quote may need about ${added:.0f} more to hit minimum profit target.")
        if miles_val >= long_drive_miles:
            suggestions.append("Long drive: add travel premium or only accept on a better cluster day.")
        if bool(r.get("can_shift")) and (miles_val >= 12 or (not math.isnan(profit) and profit < min_profit)):
            discount = min(25, max(10, round(miles_val / 2 / 5) * 5))
            suggestions.append(f"Instead of lowering price randomly, offer about ${discount} off only if client moves to a stronger cluster day.")
        if suggestions:
            rows.append({"date": r["date"], "resource": r["resource"], "client": r["client"], "current_profit": r.get("profit_score", ""), "travel_miles": miles_val, "suggestion": " ".join(suggestions)})
    return pd.DataFrame(rows)


def emergency_rescue_suggestions(unassigned: pd.DataFrame, alerts: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if not unassigned.empty:
        for _, r in unassigned.iterrows():
            rows.append({"problem": "Unassigned job", "client": r.get("client"), "date": r.get("anchor_date"), "severity": "High", "option_a": "Reassign to closest available cleaner/team", "option_b": "Move a low-priority job first", "option_c": "Offer client next-day/next-week reschedule discount", "details": r.get("reason", "")})
    if not alerts.empty:
        bad = alerts[alerts["severity"].isin(["High", "Medium"])].head(20)
        for _, r in bad.iterrows():
            rows.append({"problem": r.get("type"), "client": r.get("client"), "date": r.get("date"), "severity": r.get("severity"), "option_a": "Keep VIP/high-priority clients in place", "option_b": "Move low-priority/flexible client", "option_c": "Split crew or add buffer", "details": r.get("message", "")})
    return pd.DataFrame(rows)


def build_daily_text(schedule: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if schedule.empty:
        return pd.DataFrame()
    for (d, resource), group in schedule.groupby(["date", "resource"]):
        group = group.sort_values("start_min")
        lines = [f"Hi {resource}, here is your schedule for {pd.to_datetime(d).strftime('%A, %b %d')}:"]
        for _, r in group.iterrows():
            members = f" ({r['members']})" if r.get("resource_type") == "Crew" else ""
            lines.append(f"- {r['start']} - {r['client']} | {r['address']} | {r['cleaning_type']} | est. {r['duration_hours']} hrs{members}")
        lines.append("Please keep us updated if anything starts running behind so we can adjust the next appointment if needed.")
        rows.append({"date": d, "resource": resource, "message": "\n".join(lines)})
    return pd.DataFrame(rows)



def build_move_message_suggestions(schedule: pd.DataFrame, price_suggestions: pd.DataFrame, company_name: str = "Dynamic Duo Cleaning") -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if schedule is None or schedule.empty:
        return pd.DataFrame()
    # Rows scheduled away from their anchor date, or rows with route/price issues and flexibility.
    problem_clients = set()
    if price_suggestions is not None and not price_suggestions.empty:
        problem_clients = set(price_suggestions["client"].astype(str))
    for _, r in schedule.iterrows():
        can_shift = bool(r.get("can_shift"))
        anchor = r.get("anchor_date")
        moved = anchor not in [None, ""] and str(anchor) != str(r.get("date"))
        route_issue = str(r.get("client")) in problem_clients or float(r.get("travel_miles", 0.0)) >= 12
        if not can_shift and not moved:
            continue
        if not moved and not route_issue:
            continue
        client = str(r.get("client", "there"))
        day = str(r.get("day", "the suggested day"))
        start = str(r.get("start", ""))
        city = str(r.get("city", "your area"))
        discount = 15 if float(r.get("travel_miles", 0.0)) >= 15 else 10
        message = (
            f"Hi {client}, this is {company_name}. We are organizing our route for next week and have a cleaner/team already in the {city} area on {day}. "
            f"Would {day} around {start} work for your cleaning instead? If that adjustment works for you, we would be happy to apply a ${discount} courtesy discount for helping us align the route more efficiently."
        )
        rows.append({
            "client": client,
            "current_scheduled_date": r.get("date"),
            "suggested_day": day,
            "suggested_time": start,
            "resource": r.get("resource"),
            "reason": "Moved from anchor/preferred date" if moved else "Better route/cluster opportunity",
            "suggested_discount": f"${discount}",
            "message": message,
        })
    return pd.DataFrame(rows)


def add_manager_review_columns(schedule: pd.DataFrame) -> pd.DataFrame:
    if schedule is None or schedule.empty:
        return schedule
    review = schedule.copy()
    if "manager_status" not in review.columns:
        review.insert(0, "manager_status", "Approve")
    if "lock_assignment" not in review.columns:
        review.insert(1, "lock_assignment", False)
    if "manager_note" not in review.columns:
        review.insert(2, "manager_note", "")
    return review



def apply_review_status_to_schedule(schedule: pd.DataFrame, reviewed_subset: pd.DataFrame) -> pd.DataFrame:
    if schedule is None or schedule.empty or reviewed_subset is None or reviewed_subset.empty or "instance_id" not in reviewed_subset.columns:
        return add_manager_review_columns(schedule)
    out = add_manager_review_columns(schedule)
    status_map = reviewed_subset.set_index("instance_id")[[c for c in ["manager_status", "lock_assignment", "manager_note"] if c in reviewed_subset.columns]].to_dict("index")
    for idx, row in out.iterrows():
        iid = row.get("instance_id")
        if iid in status_map:
            for col, val in status_map[iid].items():
                out.at[idx, col] = val
    return out

def split_reviewed_schedule(reviewed: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if reviewed is None or reviewed.empty or "manager_status" not in reviewed.columns:
        return reviewed if reviewed is not None else pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    statuses = reviewed["manager_status"].astype(str).str.lower()
    rejected_mask = statuses.isin(["reject", "rejected"])
    review_mask = statuses.isin(["needs review", "review", "manual review"])
    locked_mask = reviewed.get("lock_assignment", False) == True
    approved_mask = (statuses.isin(["approve", "approved", "lock", "locked"]) | locked_mask) & ~rejected_mask & ~review_mask
    approved = reviewed[approved_mask].copy()
    rejected = reviewed[rejected_mask].copy()
    needs_review = reviewed[review_mask].copy()
    return approved, rejected, needs_review

def create_map(schedule: pd.DataFrame, points_df: pd.DataFrame) -> None:
    if schedule.empty or points_df.empty or pdk is None:
        st.info("Map will appear after a schedule is generated. Pydeck must be installed.")
        return
    color_map: Dict[str, List[int]] = {}
    resources = list(schedule["resource"].drop_duplicates())
    for i, r in enumerate(resources):
        color_map[r] = PALETTE[i % len(PALETTE)]
    map_rows: List[Dict[str, Any]] = []
    for _, r in schedule.iterrows():
        p = points_df[points_df["point_key"] == r["point_key"]]
        if p.empty:
            continue
        pr = p.iloc[0]
        map_rows.append({"lat": pr["lat"], "lng": pr["lng"], "label": f"{r['resource']} - {r['start']} - {r['client']}", "resource": r["resource"], "color": color_map.get(r["resource"], [0, 0, 0])})
    for _, p in points_df[points_df["kind"] == "Base"].iterrows():
        res_name = str(p["name"]).replace(" base", "")
        map_rows.append({"lat": p["lat"], "lng": p["lng"], "label": p["name"], "resource": res_name, "color": color_map.get(res_name, [80, 80, 80])})
    df = pd.DataFrame(map_rows)
    if df.empty:
        st.info("No map points available.")
        return
    view_state = pdk.ViewState(latitude=float(df["lat"].mean()), longitude=float(df["lng"].mean()), zoom=9, pitch=0)
    layer = pdk.Layer(
        "ScatterplotLayer",
        data=df,
        get_position="[lng, lat]",
        get_color="color",
        get_radius=500,
        pickable=True,
    )
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state, tooltip={"text": "{label}"}))


def to_excel(sheets: Dict[str, pd.DataFrame]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, df in sheets.items():
            safe = name[:31]
            if df is None or df.empty:
                pd.DataFrame({"message": ["No rows"]}).to_excel(writer, sheet_name=safe, index=False)
            else:
                df.to_excel(writer, sheet_name=safe, index=False)
    return output.getvalue()


def display_df(label: str, df: pd.DataFrame) -> None:
    st.subheader(label)
    if df is None or df.empty:
        st.info("No rows yet.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)

# -----------------------------
# Google Sheets shared master-data helpers
# -----------------------------

MASTER_SHEET_TABS = {
    "cleaners": "Cleaners",
    "crews": "Crew Rules",
    "exceptions": "Availability Exceptions",
    "area": "Area Memory",
    "actuals": "Actual Time History",
    # Shared weekly bookings used by both admins.
    "bookings": "Active Bookings",
    "active_meta": "Active Week Metadata",
    "approved": "Approved Schedule",
    "reviewed": "Reviewed Schedule",
    "manager_review": "Manager Review",
    "alerts": "Alerts",
}


def _secret_section_to_dict(section_name: str) -> Dict[str, Any]:
    try:
        section = st.secrets.get(section_name, None)
        if not section:
            return {}
        # Streamlit returns an AttrDict-like object. Convert it safely.
        return {k: v for k, v in dict(section).items()}
    except Exception:
        return {}


def get_google_sheet_id() -> str:
    gs = _secret_section_to_dict("google_sheets")
    for candidate in [gs.get("spreadsheet_id"), get_secret_value("GOOGLE_SHEET_ID"), os.environ.get("GOOGLE_SHEET_ID")]:
        if candidate:
            return str(candidate).strip()
    return ""


def get_google_service_account_info() -> Dict[str, Any]:
    # Preferred Streamlit secrets format:
    # [gcp_service_account]
    # type = "service_account"
    # project_id = "..."
    # private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
    # client_email = "..."
    for section_name in ("gcp_service_account", "google_service_account"):
        info = _secret_section_to_dict(section_name)
        if info:
            break
    else:
        raw = get_secret_value("GOOGLE_SERVICE_ACCOUNT_JSON") or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        if raw:
            try:
                info = json.loads(raw)
            except Exception:
                info = {}
        else:
            info = {}
    if info and "private_key" in info and isinstance(info["private_key"], str):
        info["private_key"] = info["private_key"].replace("\\n", "\n")
    return info


@st.cache_resource(show_spinner=False)
def get_gspread_client(service_account_key: str):
    """Authorize Google Sheets access from Streamlit secrets without storing JSON files in GitHub."""
    import gspread
    from google.oauth2.service_account import Credentials

    info = json.loads(service_account_key)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)


def google_sheets_configured(sheet_id: str = "") -> Tuple[bool, str]:
    info = get_google_service_account_info()
    sid = sheet_id or get_google_sheet_id()
    missing = []
    if not sid:
        missing.append("Google Sheet ID")
    for field in ["client_email", "private_key", "token_uri"]:
        if not info.get(field):
            missing.append(field)
    if missing:
        return False, "Missing: " + ", ".join(missing)
    return True, "Google Sheets configured"


def read_google_tab(sheet_id: str, tab_name: str, fallback: Optional[pd.DataFrame] = None) -> Tuple[pd.DataFrame, str]:
    fallback = fallback if fallback is not None else pd.DataFrame()
    ok, msg = google_sheets_configured(sheet_id)
    if not ok:
        return fallback.copy(), msg
    try:
        info = get_google_service_account_info()
        client = get_gspread_client(json.dumps(info, sort_keys=True))
        sh = client.open_by_key(sheet_id)
        ws = sh.worksheet(tab_name)
        values = ws.get_all_values()
        if not values:
            return fallback.copy(), f"{tab_name}: empty, using fallback"
        header = [clean_col(x) for x in values[0]]
        rows = values[1:]
        # Keep blank columns out, but preserve all user-entered columns.
        keep = [i for i, h in enumerate(header) if h]
        header = [header[i] for i in keep]
        cleaned_rows = [[row[i] if i < len(row) else "" for i in keep] for row in rows if any(str(x).strip() for x in row)]
        if not header:
            return fallback.copy(), f"{tab_name}: no header row, using fallback"
        return pd.DataFrame(cleaned_rows, columns=header), f"{tab_name}: loaded {len(cleaned_rows)} row(s)"
    except Exception as exc:
        return fallback.copy(), f"{tab_name}: could not load ({exc})"


def read_uploaded_csv(uploaded_file: Any) -> pd.DataFrame:
    """Read a Streamlit uploaded CSV without consuming it for later reads."""
    if uploaded_file is None:
        return pd.DataFrame()
    try:
        uploaded_file.seek(0)
    except Exception:
        pass
    df = pd.read_csv(uploaded_file)
    try:
        uploaded_file.seek(0)
    except Exception:
        pass
    return df


def make_active_week_metadata(week_start: date, horizon_weeks: int, include_weekends: bool, uploaded_by: str, row_count: int, source_name: str = "BookingKoala/GHL CSV") -> pd.DataFrame:
    week_end = week_start + timedelta(days=(7 * int(horizon_weeks)) - 1)
    return pd.DataFrame([
        {"field": "active_week_start", "value": week_start.isoformat()},
        {"field": "active_week_end", "value": week_end.isoformat()},
        {"field": "planning_horizon_weeks", "value": str(horizon_weeks)},
        {"field": "include_weekends", "value": "Yes" if include_weekends else "No"},
        {"field": "uploaded_by", "value": uploaded_by.strip() or "Admin"},
        {"field": "uploaded_at", "value": datetime.now().isoformat(timespec="seconds")},
        {"field": "source", "value": source_name},
        {"field": "row_count", "value": str(row_count)},
    ])


def stamp_active_bookings(df: pd.DataFrame, week_start: date, horizon_weeks: int, include_weekends: bool, uploaded_by: str) -> pd.DataFrame:
    """Add lightweight metadata columns without changing the BookingKoala/GHL fields."""
    out = df.copy()
    week_end = week_start + timedelta(days=(7 * int(horizon_weeks)) - 1)
    # Remove old upload metadata first to avoid duplicate columns after repeated saves.
    out = out.drop(columns=[c for c in ["active_week_start", "active_week_end", "uploaded_by", "uploaded_at"] if c in out.columns], errors="ignore")
    out.insert(0, "active_week_start", week_start.isoformat())
    out.insert(1, "active_week_end", week_end.isoformat())
    out.insert(2, "uploaded_by", uploaded_by.strip() or "Admin")
    out.insert(3, "uploaded_at", datetime.now().isoformat(timespec="seconds"))
    return out


def load_master_data(sheet_id: str, use_sheets: bool, files: Dict[str, Any]) -> Tuple[Dict[str, pd.DataFrame], List[str]]:
    """Load weekly data. Uploaded BookingKoala/GHL CSV overrides the shared Active Bookings tab for this run."""
    statuses: List[str] = []
    defaults = {
        "cleaners": default_cleaners(),
        "crews": default_crews(),
        "exceptions": pd.DataFrame(),
        "area": default_area_memory(),
        "actuals": pd.DataFrame(),
        "bookings": default_bookings(),
    }
    out: Dict[str, pd.DataFrame] = {}

    # Weekly bookings can be uploaded and/or saved as a shared Active Week in Google Sheets.
    if files.get("bookings") is not None:
        out["bookings"] = read_uploaded_csv(files["bookings"])
        statuses.append("Bookings: loaded from uploaded BookingKoala/GHL CSV for this run")
    elif use_sheets:
        out["bookings"], status = read_google_tab(sheet_id, MASTER_SHEET_TABS["bookings"], defaults["bookings"])
        statuses.append(status.replace(MASTER_SHEET_TABS["bookings"], "Active Bookings"))
    else:
        out["bookings"] = defaults["bookings"]
        statuses.append("Bookings: using sample data")

    for name, tab in [("cleaners", "Cleaners"), ("crews", "Crew Rules"), ("exceptions", "Availability Exceptions"), ("area", "Area Memory"), ("actuals", "Actual Time History")]:
        if files.get(name) is not None:
            out[name] = read_uploaded_csv(files[name])
            statuses.append(f"{tab}: loaded from uploaded CSV override")
        elif use_sheets:
            fallback = defaults[name]
            if name == "actuals" and files.get("bookings") is None:
                fallback = default_actuals()
            out[name], status = read_google_tab(sheet_id, tab, fallback)
            statuses.append(status)
        else:
            if name == "actuals":
                out[name] = default_actuals() if files.get("bookings") is None else pd.DataFrame()
            else:
                out[name] = defaults[name]
            statuses.append(f"{tab}: using {'sample/default' if name != 'exceptions' else 'blank'} data")
    return out, statuses


def make_sheet_safe_values(df: pd.DataFrame) -> List[List[Any]]:
    if df is None or df.empty:
        return [["message"], ["No rows"]]
    temp = df.copy()
    for col in temp.columns:
        temp[col] = temp[col].map(lambda x: "" if pd.isna(x) else (x.isoformat() if hasattr(x, "isoformat") and not isinstance(x, str) else x))
    return [list(temp.columns)] + temp.astype(str).values.tolist()


def write_google_tab(sheet_id: str, tab_name: str, df: pd.DataFrame) -> str:
    ok, msg = google_sheets_configured(sheet_id)
    if not ok:
        return msg
    info = get_google_service_account_info()
    client = get_gspread_client(json.dumps(info, sort_keys=True))
    sh = client.open_by_key(sheet_id)
    values = make_sheet_safe_values(df)
    try:
        ws = sh.worksheet(tab_name)
    except Exception:
        ws = sh.add_worksheet(title=tab_name, rows=max(100, len(values) + 20), cols=max(10, len(values[0]) + 2))
    ws.clear()
    # gspread signatures differ by version, so try the safest named-argument form first.
    try:
        ws.update(values=values, range_name="A1", value_input_option="USER_ENTERED")
    except TypeError:
        try:
            ws.update(values, "A1", value_input_option="USER_ENTERED")
        except TypeError:
            ws.update(values)
    return f"Saved {len(values)-1} row(s) to {tab_name}."

# -----------------------------
# Sample/default data
# -----------------------------

def default_cleaners() -> pd.DataFrame:
    return pd.DataFrame([
        {"cleaner": "Isabel", "base_address": "Burnsville, MN", "available_days": "Monday,Tuesday,Wednesday,Thursday,Friday", "max_jobs_per_day": 4, "max_hours_per_day": 8, "start_time": "08:30", "end_time": "17:00", "hourly_cost": 23, "allow_solo": "No"},
        {"cleaner": "Jacky", "base_address": "Burnsville, MN", "available_days": "Monday,Tuesday,Wednesday,Thursday,Friday", "max_jobs_per_day": 4, "max_hours_per_day": 8, "start_time": "08:30", "end_time": "17:00", "hourly_cost": 23, "allow_solo": "No"},
        {"cleaner": "Billy", "base_address": "Belle Plaine, MN", "available_days": "Monday,Tuesday,Wednesday,Thursday,Friday", "max_jobs_per_day": 3, "max_hours_per_day": 8, "start_time": "08:30", "end_time": "17:00", "hourly_cost": 22, "allow_solo": "Yes"},
        {"cleaner": "Eduardo", "base_address": "St. Louis Park, MN", "available_days": "Monday,Tuesday,Wednesday,Thursday,Friday", "max_jobs_per_day": 3, "max_hours_per_day": 8, "start_time": "08:30", "end_time": "17:00", "hourly_cost": 24, "allow_solo": "Yes"},
        {"cleaner": "Klarisa", "base_address": "Eagan, MN", "available_days": "Monday,Tuesday,Wednesday,Thursday,Friday", "max_jobs_per_day": 3, "max_hours_per_day": 8, "start_time": "08:30", "end_time": "17:00", "hourly_cost": 40, "allow_solo": "Yes"},
        {"cleaner": "Chloe", "base_address": "Apple Valley, MN", "available_days": "Monday,Tuesday,Wednesday,Thursday,Friday", "max_jobs_per_day": 3, "max_hours_per_day": 8, "start_time": "08:30", "end_time": "17:00", "hourly_cost": 22, "allow_solo": "Yes"},
    ])


def default_crews() -> pd.DataFrame:
    return pd.DataFrame([
        {"resource_name": "Isabel/Jacky", "members": "Isabel;Jacky", "team_type": "Fixed", "available_days": "Monday,Tuesday,Wednesday,Thursday,Friday", "base_address": "Burnsville, MN", "can_split_after_job": "No", "always_together": "Yes", "carpool": "Yes", "max_jobs_per_day": 4, "max_hours_per_day": 8, "start_time": "08:30", "end_time": "17:00", "productivity_multiplier": 2, "hourly_cost_override": ""},
        {"resource_name": "Billy/Eduardo", "members": "Billy;Eduardo", "team_type": "Optional", "available_days": "Monday,Tuesday,Wednesday,Thursday,Friday", "base_address": "St. Louis Park, MN", "can_split_after_job": "Yes", "always_together": "No", "carpool": "Yes", "max_jobs_per_day": 3, "max_hours_per_day": 8, "start_time": "08:30", "end_time": "17:00", "productivity_multiplier": 2, "hourly_cost_override": ""},
    ])


def default_bookings() -> pd.DataFrame:
    return pd.DataFrame([
        {"client": "Carrie Herbeck", "address": "New Prague, MN", "service_date": "", "preferred_day": "Wednesday", "flexible_days": "Tuesday,Wednesday,Thursday", "time_window": "Flexible", "earliest_start": "08:30", "latest_finish": "17:00", "job_hours": 4.0, "cleaning_type": "Standard", "can_shift": "Yes", "frequency": "Biweekly", "job_price": 270, "preferred_resource": "", "lock_resource": "No", "priority": "High", "risk_level": "Medium", "buffer_minutes": "", "min_workers": 1, "max_workers": 2, "requires_team": "No", "notes": "Sample recurring client"},
        {"client": "Dan Konicek", "address": "Eagan, MN", "service_date": "", "preferred_day": "Thursday", "flexible_days": "Wednesday,Thursday", "time_window": "Morning", "earliest_start": "08:30", "latest_finish": "12:30", "job_hours": 3.0, "cleaning_type": "Deep Cleaning", "can_shift": "Yes", "frequency": "Monthly", "job_price": 330, "preferred_resource": "Klarisa", "lock_resource": "No", "priority": "Normal", "risk_level": "High", "buffer_minutes": "", "min_workers": 1, "max_workers": 2, "requires_team": "No", "notes": "Sample deep clean"},
        {"client": "Lakeville Client", "address": "Lakeville, MN", "service_date": "", "preferred_day": "Tuesday", "flexible_days": "Tuesday,Thursday", "time_window": "Flexible", "earliest_start": "08:30", "latest_finish": "17:00", "job_hours": 2.5, "cleaning_type": "Standard", "can_shift": "Yes", "frequency": "One time", "job_price": 205, "preferred_resource": "Chloe", "lock_resource": "Yes", "priority": "VIP", "risk_level": "Low", "buffer_minutes": "", "min_workers": 1, "max_workers": 1, "requires_team": "No", "notes": "Locked recurring-style example"},
    ])


def default_area_memory() -> pd.DataFrame:
    return pd.DataFrame([
        {"area_keyword": "Lakeville", "best_days": "Tuesday,Thursday", "avoid_days": "Friday", "preferred_resources": "Chloe,Klarisa", "notes": "Good south-metro cluster"},
        {"area_keyword": "Eagan", "best_days": "Wednesday,Thursday", "avoid_days": "", "preferred_resources": "Klarisa,Chloe,Isabel/Jacky", "notes": "Easy for Eagan/Apple Valley/Burnsville routes"},
        {"area_keyword": "New Prague", "best_days": "Wednesday", "avoid_days": "Monday,Friday", "preferred_resources": "Billy,Isabel/Jacky", "notes": "Farther route; avoid one-off trips"},
    ])



def default_actuals() -> pd.DataFrame:
    return pd.DataFrame([
        {"client": "Carrie Herbeck", "address": "New Prague, MN", "service_date": "", "cleaner": "Billy", "estimated_hours": 4.0, "actual_hours": 4.75, "notes": "Larger home; add more buffer next time"},
        {"client": "Dan Konicek", "address": "Eagan, MN", "service_date": "", "cleaner": "Klarisa", "estimated_hours": 3.0, "actual_hours": 4.0, "notes": "Deep cleaning took longer than expected"},
        {"client": "Lakeville Client", "address": "Lakeville, MN", "service_date": "", "cleaner": "Chloe", "estimated_hours": 2.5, "actual_hours": 2.5, "notes": "Estimate was accurate"},
    ])

def default_exceptions() -> pd.DataFrame:
    return pd.DataFrame([
        {"cleaner": "Klarisa", "date": "", "day": "", "status": "", "available_start": "", "available_end": "", "reason": ""},
    ])

# -----------------------------
# Streamlit UI
# -----------------------------

def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption("Easy weekly route planning for cleaners, crews, recurring clients, emergencies, and profit-aware scheduling.")

    with st.sidebar:
        st.header("Setup")
        api_key_default = get_secret_value("GOOGLE_MAPS_API_KEY")
        api_key = st.text_input("Google Maps API key", value=api_key_default, type="password", help="Optional for testing. Without it, the app uses approximate demo distances.")
        use_google = st.checkbox("Use Google driving routes", value=bool(api_key_default), help="Turn off while testing to avoid API usage.")
        week_start = st.date_input("Week start", value=date.today())
        horizon_weeks = st.slider("Planning horizon", 1, 8, 2, help="Use 4-8 weeks to catch recurring conflicts before they happen.")
        include_weekends = st.checkbox("Include weekends", value=False)
        hours_are_person_hours = st.checkbox("Job hours are one-person hours", value=True, help="If checked, a 4-hour job with 2 cleaners becomes about 2 hours plus buffer.")
        mileage_cost = st.number_input("Mileage cost per mile", value=0.67, min_value=0.0, step=0.05)
        travel_hour_cost = st.number_input("Travel time cost per hour", value=15.0, min_value=0.0, step=1.0)
        min_profit = st.number_input("Minimum target profit per job", value=70.0, min_value=0.0, step=10.0)
        long_drive_miles = st.number_input("Bad route warning if drive leg exceeds miles", value=22.0, min_value=1.0, step=1.0)

        st.divider()
        st.header("Shared master data")
        sheet_id_default = get_google_sheet_id()
        sheet_id = st.text_input("Google Sheet ID", value=sheet_id_default, type="password", help="Optional. Store this in Streamlit secrets for production.")
        sheets_ok, sheets_msg = google_sheets_configured(sheet_id)
        use_google_sheets_master = st.checkbox(
            "Use Google Sheets shared data",
            value=sheets_ok,
            help="If checked, the app reads Cleaners, Crew Rules, Availability Exceptions, Area Memory, Actual Time History, and Active Bookings from the shared Sheet unless you upload a CSV override.",
        )
        if use_google_sheets_master:
            if sheets_ok:
                st.success("Google Sheets shared data ready")
            else:
                st.warning(sheets_msg)

    st.markdown("### Weekly BookingKoala/GHL upload + shared active week")
    st.write("Upload the current week from BookingKoala/GHL. You can save that upload as the shared Active Week in Google Sheets so both admins load the same bookings without uploading again.")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        cleaners_file = st.file_uploader("Cleaners CSV override", type=["csv"], key="cleaners")
    with c2:
        bookings_file = st.file_uploader("BookingKoala/GHL bookings CSV", type=["csv"], key="bookings")
    with c3:
        crew_file = st.file_uploader("Crew rules CSV override", type=["csv"], key="crews")
    with c4:
        exceptions_file = st.file_uploader("Day off / emergency CSV override", type=["csv"], key="exceptions")
    with c5:
        area_file = st.file_uploader("Area memory CSV override", type=["csv"], key="area")
    with c6:
        actuals_file = st.file_uploader("Actual time history CSV override", type=["csv"], key="actuals")

    st.divider()
    st.subheader("Shared Active Week")
    aw1, aw2, aw3 = st.columns([1.4, 1.2, 1.4])
    with aw1:
        uploaded_by = st.text_input("Admin name / initials", value="", help="Used only for the Active Week upload log.")
    with aw2:
        st.caption("Current planning window")
        st.write(f"{week_start.isoformat()} → {(week_start + timedelta(days=(7 * int(horizon_weeks)) - 1)).isoformat()}")
    with aw3:
        if bookings_file is None:
            st.info("No bookings CSV uploaded. If Google Sheets is connected, the app will load the shared Active Bookings tab.")
        elif use_google_sheets_master and google_sheets_configured(sheet_id)[0]:
            uploaded_bookings_preview = read_uploaded_csv(bookings_file)
            st.success(f"Uploaded {len(uploaded_bookings_preview)} booking row(s) for this run.")
            if st.button("Save uploaded CSV as Active Week for both admins", type="primary"):
                active_bookings = stamp_active_bookings(uploaded_bookings_preview, week_start, horizon_weeks, include_weekends, uploaded_by)
                meta = make_active_week_metadata(week_start, horizon_weeks, include_weekends, uploaded_by, len(active_bookings))
                msg1 = write_google_tab(sheet_id, MASTER_SHEET_TABS["bookings"], active_bookings)
                msg2 = write_google_tab(sheet_id, MASTER_SHEET_TABS["active_meta"], meta)
                st.success(msg1)
                st.success(msg2)
                st.info("The other admin can now open the app, keep the bookings uploader empty, and load the same Active Week from Google Sheets.")
        else:
            st.warning("Connect Google Sheets to save this upload as the shared Active Week. The CSV will still work for your current session.")

    if use_google_sheets_master and google_sheets_configured(sheet_id)[0]:
        active_meta_df, active_meta_status = read_google_tab(sheet_id, MASTER_SHEET_TABS["active_meta"], pd.DataFrame())
        if not active_meta_df.empty and set(["field", "value"]).issubset(active_meta_df.columns):
            meta_dict = dict(zip(active_meta_df["field"], active_meta_df["value"]))
            st.caption(
                "Active Week in Google Sheets: "
                f"{meta_dict.get('active_week_start', 'unknown')} → {meta_dict.get('active_week_end', 'unknown')} "
                f"| uploaded by {meta_dict.get('uploaded_by', 'Admin')} "
                f"| rows: {meta_dict.get('row_count', '0')}"
            )

    try:
        files = {"cleaners": cleaners_file, "bookings": bookings_file, "crews": crew_file, "exceptions": exceptions_file, "area": area_file, "actuals": actuals_file}
        master_data, load_statuses = load_master_data(sheet_id, use_google_sheets_master, files)
        cleaners_raw = master_data["cleaners"]
        bookings_raw = master_data["bookings"]
        crews_raw = master_data["crews"]
        exceptions_raw = master_data["exceptions"]
        area_raw = master_data["area"]
        actuals_raw = master_data["actuals"]
        with st.expander("Data source status", expanded=False):
            for status in load_statuses:
                st.write("- " + status)

        # Quick emergency override.
        with st.expander("Quick emergency / day-off override"):
            e1, e2, e3, e4 = st.columns(4)
            with e1:
                emergency_cleaner = st.selectbox("Cleaner/resource", [""] + list(cleaners_raw.get("cleaner", pd.Series(dtype=str)).astype(str)) + list(crews_raw.get("resource_name", pd.Series(dtype=str)).astype(str)))
            with e2:
                emergency_date = st.date_input("Affected date", value=week_start, key="emergency_date")
            with e3:
                emergency_type = st.selectbox("Status", ["None", "Full day off / emergency", "Morning off", "Afternoon off", "Custom hours"])
            with e4:
                reason = st.text_input("Reason/note", value="Emergency override")
            custom_start, custom_end = "", ""
            if emergency_type == "Custom hours":
                cc1, cc2 = st.columns(2)
                with cc1:
                    custom_start = st.text_input("Available start", value="12:00")
                with cc2:
                    custom_end = st.text_input("Available end", value="17:00")
            if emergency_cleaner and emergency_type != "None":
                if emergency_type == "Full day off / emergency":
                    row = {"cleaner": emergency_cleaner, "date": emergency_date.isoformat(), "day": "", "status": "Emergency", "available_start": "", "available_end": "", "reason": reason}
                elif emergency_type == "Morning off":
                    row = {"cleaner": emergency_cleaner, "date": emergency_date.isoformat(), "day": "", "status": "Half Day", "available_start": "12:00", "available_end": DEFAULT_END, "reason": reason}
                elif emergency_type == "Afternoon off":
                    row = {"cleaner": emergency_cleaner, "date": emergency_date.isoformat(), "day": "", "status": "Half Day", "available_start": DEFAULT_START, "available_end": "12:30", "reason": reason}
                else:
                    row = {"cleaner": emergency_cleaner, "date": emergency_date.isoformat(), "day": "", "status": "Custom", "available_start": custom_start, "available_end": custom_end, "reason": reason}
                exceptions_raw = pd.concat([exceptions_raw, pd.DataFrame([row])], ignore_index=True)
                st.success("Emergency override added to this optimization run.")

        cleaners = prepare_cleaners(cleaners_raw)
        bookings = prepare_bookings(bookings_raw)
        actuals = prepare_actuals(actuals_raw)
        bookings, time_learning = apply_actual_time_learning(bookings, actuals)
        crews = prepare_crew_rules(crews_raw)
        exceptions = prepare_availability_exceptions(exceptions_raw)
        area_memory = prepare_area_memory(area_raw)
        resources, resource_lookup = build_resources(cleaners, crews)
        dates = week_dates(week_start, horizon_weeks, include_weekends)
        booking_instances = expand_recurring_bookings(bookings, dates)

        if booking_instances.empty:
            st.warning("No bookings found inside this planning horizon.")
            return

        with st.spinner("Preparing locations and optimizing schedule..."):
            points, point_idx, points_df = make_points(resources, booking_instances, api_key, use_google)
            miles_matrix, minutes_matrix, route_source = compute_route_matrix(points, api_key, use_google)
            schedule, unassigned, alerts, schedules_dict, member_events = optimize_schedule(
                booking_instances, resources, dates, exceptions, miles_matrix, minutes_matrix, point_idx,
                area_memory, hours_are_person_hours, mileage_cost, travel_hour_cost,
            )
            price_suggestions = price_adjustment_suggestions(schedule, min_profit, long_drive_miles)
            move_messages = build_move_message_suggestions(schedule, price_suggestions)
            rescue = emergency_rescue_suggestions(unassigned, alerts, schedule)
            texts = build_daily_text(schedule)

        st.success(f"Optimization completed using {route_source}.")

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Scheduled jobs", len(schedule))
        k2.metric("Unassigned", len(unassigned))
        k3.metric("Alerts", len(alerts))
        k4.metric("Resources", len(resources))
        total_miles = float(schedule["travel_miles"].astype(float).sum()) if not schedule.empty else 0.0
        k5.metric("Drive miles before jobs", f"{total_miles:.1f}")

        reviewed_schedule = add_manager_review_columns(schedule)
        approved_schedule, rejected_schedule, needs_review_schedule = split_reviewed_schedule(reviewed_schedule)

        tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
            "Review + schedule", "Crews/resources", "New booking checker", "Alerts + emergency", "Map", "Cleaner texts", "Exports", "Templates"
        ])

        with tab1:
            display_cols = ["manager_status", "lock_assignment", "manager_note", "date", "day", "resource", "resource_type", "members", "start", "end", "client", "city", "cleaning_type", "priority", "duration_hours", "travel_miles", "travel_minutes", "profit_score", "buffer_mins", "time_learning_note", "score_notes", "instance_id"]
            review_cols = [c for c in display_cols if c in reviewed_schedule.columns]
            st.subheader("Manager approval")
            st.caption("Review the recommended schedule, then approve, lock, reject, or mark rows for manual review before exporting.")
            if reviewed_schedule.empty:
                st.info("No scheduled jobs yet.")
            else:
                reviewed_subset = st.data_editor(
                    reviewed_schedule[review_cols],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "manager_status": st.column_config.SelectboxColumn("Status", options=["Approve", "Lock", "Needs Review", "Reject"], required=True),
                        "lock_assignment": st.column_config.CheckboxColumn("Lock"),
                        "manager_note": st.column_config.TextColumn("Manager note"),
                    },
                    disabled=[c for c in review_cols if c not in {"manager_status", "lock_assignment", "manager_note"}],
                    key="manager_review_editor",
                )
                reviewed_schedule = apply_review_status_to_schedule(schedule, reviewed_subset)
                approved_schedule, rejected_schedule, needs_review_schedule = split_reviewed_schedule(reviewed_schedule)
                a1, a2, a3 = st.columns(3)
                a1.metric("Approved/locked", len(approved_schedule))
                a2.metric("Needs review", len(needs_review_schedule))
                a3.metric("Rejected", len(rejected_schedule))
                display_df("Final approved schedule for export", approved_schedule[[c for c in display_cols if c in approved_schedule.columns]].drop(columns=["instance_id"], errors="ignore"))
                display_df("Rejected / manual review rows", pd.concat([rejected_schedule, needs_review_schedule], ignore_index=True)[[c for c in display_cols if c in reviewed_schedule.columns]].drop(columns=["instance_id"], errors="ignore") if (not rejected_schedule.empty or not needs_review_schedule.empty) else pd.DataFrame())
            display_df("Unassigned / needs manual review", unassigned)
            display_df("Actual-vs-estimated learning applied", time_learning)

        with tab2:
            display_df("Available resources created from cleaners + crew rules", resources.drop(columns=["member_keys"], errors="ignore"))
            st.info("Fixed crews, like Isabel/Jacky, are treated as one route resource. Optional crews, like Billy/Eduardo, can be used when it helps, while the app prevents overlapping schedules for the same person.")

        with tab3:
            st.subheader("New booking checker")
            st.write("Use this before confirming a new job. It checks where the new client fits best against the already optimized schedule.")
            n1, n2, n3, n4 = st.columns(4)
            with n1:
                nb_client = st.text_input("Client name", value="New Lead")
                nb_address = st.text_input("Address/city", value="Lakeville, MN")
            with n2:
                nb_type = st.selectbox("Cleaning type", ["Standard", "Deep Cleaning", "Move Out", "Airbnb"])
                nb_hours = st.number_input("One-person job hours", value=3.0, min_value=0.5, step=0.5)
            with n3:
                nb_price = st.number_input("Quoted price", value=240.0, min_value=0.0, step=10.0)
                nb_days = st.text_input("Flexible days", value="Tuesday,Wednesday,Thursday")
            with n4:
                nb_window = st.selectbox("Time window", ["Flexible", "Morning", "Afternoon", "Fixed"])
                nb_priority = st.selectbox("Priority", ["VIP", "High", "Normal", "Low"], index=2)
            if st.button("Check best fit"):
                new_raw = pd.DataFrame([{ "client": nb_client, "address": nb_address, "service_date": "", "preferred_day": "", "flexible_days": nb_days, "time_window": nb_window, "earliest_start": DEFAULT_START, "latest_finish": DEFAULT_END, "job_hours": nb_hours, "cleaning_type": nb_type, "can_shift": "Yes", "frequency": "One time", "job_price": nb_price, "preferred_resource": "", "lock_resource": "No", "priority": nb_priority, "risk_level": "Medium" if nb_type == "Deep Cleaning" else "Low", "min_workers": 1, "max_workers": 2, "requires_team": "No" }])
                new_booking = prepare_bookings(new_raw)
                temp_instances = expand_recurring_bookings(new_booking, dates)
                # Add its point to existing points and recompute small matrix for simplicity.
                all_instances = pd.concat([booking_instances, temp_instances], ignore_index=True)
                pts2, idx2, ptsdf2 = make_points(resources, all_instances, api_key, use_google)
                m2, t2, _ = compute_route_matrix(pts2, api_key, use_google)
                suggestions: List[Dict[str, Any]] = []
                for _, row in temp_instances.iterrows():
                    for d in candidate_dates_for_booking(row, dates):
                        for _, res in resources.iterrows():
                            ok, cand = evaluate_candidate(row, res.to_dict(), d, schedules_dict, member_events, exceptions, m2, t2, idx2, area_memory, hours_are_person_hours, mileage_cost, travel_hour_cost)
                            if ok:
                                suggestions.append(cand)
                sug_df = pd.DataFrame(suggestions).sort_values("score").head(12) if suggestions else pd.DataFrame()
                display_df("Best options for this new booking", sug_df[[c for c in ["date", "day", "resource", "resource_type", "members", "start", "end", "duration_hours", "travel_miles", "profit_score", "score", "score_notes"] if c in sug_df.columns]] if not sug_df.empty else sug_df)
                if not sug_df.empty:
                    best = sug_df.iloc[0]
                    st.success(f"Best fit: {best['day']} with {best['resource']} around {best['start']}. Estimated drive before job: {best['travel_miles']} miles.")

        with tab4:
            display_df("Late-running / bad route / price alerts", alerts)
            display_df("Price adjustment suggestions", price_suggestions)
            display_df("Auto-message suggestions for moving clients", move_messages)
            display_df("Emergency rescue mode suggestions", rescue)
            st.info("Emergency mode is designed to protect VIP/high-priority clients first, then move flexible or low-priority jobs when someone calls off or a route becomes too tight.")

        with tab5:
            create_map(schedule, points_df)
            with st.expander("Location/geocoding source"):
                display_df("Points", points_df)

        with tab6:
            approved_texts = build_daily_text(approved_schedule)
            if approved_texts.empty:
                st.info("No approved cleaner messages yet. Approve rows in the Review + schedule tab first.")
            else:
                for _, r in approved_texts.iterrows():
                    st.markdown(f"**{r['resource']} — {pd.to_datetime(r['date']).strftime('%A, %b %d')}**")
                    st.code(r["message"], language="text")

        with tab7:
            sheets = {
                "Approved Schedule": approved_schedule,
                "Full Reviewed Schedule": reviewed_schedule,
                "Rejected Review Rows": pd.concat([rejected_schedule, needs_review_schedule], ignore_index=True) if (not rejected_schedule.empty or not needs_review_schedule.empty) else pd.DataFrame(),
                "Original Recommendation": schedule,
                "Unassigned": unassigned,
                "Alerts": alerts,
                "Price Suggestions": price_suggestions,
                "Move Messages": move_messages,
                "Emergency Rescue": rescue,
                "Actual Time Learning": time_learning,
                "Actuals Uploaded": actuals,
                "Cleaner Texts": approved_texts,
                "Resources": resources.drop(columns=["member_keys"], errors="ignore"),
                "Points": points_df,
                "Active Bookings Source": bookings_raw,
            }
            excel = to_excel(sheets)
            st.download_button("Download Excel report", data=excel, file_name="dynamic_duo_schedule_report_v7.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            if not approved_schedule.empty:
                st.download_button("Download approved schedule CSV", data=approved_schedule.to_csv(index=False), file_name="approved_schedule_v7.csv", mime="text/csv")
            if not reviewed_schedule.empty:
                st.download_button("Download full reviewed schedule CSV", data=reviewed_schedule.to_csv(index=False), file_name="reviewed_schedule_v7.csv", mime="text/csv")

            st.divider()
            st.subheader("Save back to Google Sheets")
            st.caption("This is optional. Use it to keep both admins aligned after review. BookingKoala remains the official calendar until you manually update it there.")
            if use_google_sheets_master and google_sheets_configured(sheet_id)[0]:
                col_save1, col_save2, col_save3 = st.columns(3)
                with col_save1:
                    if st.button("Save approved schedule to Sheet"):
                        st.success(write_google_tab(sheet_id, MASTER_SHEET_TABS["approved"], approved_schedule))
                with col_save2:
                    if st.button("Save reviewed schedule to Sheet"):
                        st.success(write_google_tab(sheet_id, MASTER_SHEET_TABS["reviewed"], reviewed_schedule))
                with col_save3:
                    if st.button("Save current alerts to Sheet"):
                        st.success(write_google_tab(sheet_id, MASTER_SHEET_TABS["alerts"], alerts))
            else:
                st.info("Connect Google Sheets in the sidebar to enable save-back buttons.")

        with tab8:
            st.subheader("Download templates")
            templates = {
                "cleaners_template.csv": default_cleaners(),
                "bookings_template.csv": default_bookings(),
                "crew_rules_template.csv": default_crews(),
                "availability_exceptions_template.csv": default_exceptions(),
                "area_memory_template.csv": default_area_memory(),
                "actual_time_history_template.csv": default_actuals(),
                "bookingkoala_import_template.csv": default_bookings().rename(columns={"client": "customer_full_name", "address": "appointment_address", "service_date": "booking_start_date", "cleaning_type": "service_type", "job_price": "appointment_total"}),
                "ghl_import_template.csv": default_bookings().rename(columns={"client": "full_name", "address": "service_address", "cleaning_type": "service_type", "job_price": "quoted_price"}),
            }
            template_path = "dynamic_duo_google_sheets_master_template.xlsx"
            if os.path.exists(template_path):
                with open(template_path, "rb") as fh:
                    st.download_button(
                        "dynamic_duo_google_sheets_master_template.xlsx",
                        data=fh.read(),
                        file_name="dynamic_duo_google_sheets_master_template.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
            for fname, df in templates.items():
                st.download_button(fname, data=df.to_csv(index=False), file_name=fname, mime="text/csv")
            st.markdown("""
            **Important setup notes for v7**
            - Google Sheets can now be the shared source for `Cleaners`, `Crew Rules`, `Availability Exceptions`, `Area Memory`, `Actual Time History`, and `Active Bookings`.
            - Weekly BookingKoala/GHL bookings can be uploaded once and saved as the shared `Active Bookings` tab.
            - When the other admin opens the app with no bookings CSV uploaded, the app loads `Active Bookings` from Google Sheets.
            - Uploaded CSVs override the Google Sheet for that specific optimization run until you click **Save uploaded CSV as Active Week**.
            - Use `Approved Schedule`, `Reviewed Schedule`, and `Alerts` save-back buttons in the Export tab when both admins need to see the same result.
            - `crew_rules.csv`: fixed and optional teams, like Isabel/Jacky or Billy/Eduardo.
            - `area_memory.csv`: best days/resources for Lakeville, Eagan, New Prague, etc.
            - `priority`: VIP, High, Normal, Low.
            - `min_workers`, `max_workers`, `requires_team`: controls solo vs crew assignment.
            - `risk_level` and `buffer_minutes`: protects the next job from late-running homes.
            - `actual_time_history.csv`: compares estimated vs actual hours and learns extra buffer for future jobs.
            - Manager review: approve, lock, reject, or mark schedule rows for manual review before exporting.
            - Auto-message suggestions: copy/paste messages for moving flexible clients to better cluster days.
            """)

    except Exception as exc:
        st.error(f"Could not optimize schedule: {exc}")
        st.exception(exc)


if __name__ == "__main__":
    main()
