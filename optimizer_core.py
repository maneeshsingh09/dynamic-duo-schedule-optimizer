"""
Dynamic Duo Cleaning - Schedule Optimizer v17

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

APP_TITLE = "Dynamic Duo Cleaning - Schedule Optimizer v17"
DEFAULT_START = "08:30"
DEFAULT_END = "17:00"
DEFAULT_MIN_GAP_MINUTES = 30
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

def parse_datetime_any(value: Any) -> Optional[datetime]:
    """Parse ISO or common BookingKoala date/time values."""
    if pd.isna(value) or str(value).strip() == "":
        return None
    raw = str(value).strip()
    try:
        if "T" in raw or re.search(r"\d{4}-\d{2}-\d{2}", raw):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        pass
    try:
        parsed = pd.to_datetime(raw, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.to_pydatetime()
    except Exception:
        return None


def datetime_diff_minutes(start_value: Any, end_value: Any) -> float:
    start = parse_datetime_any(start_value)
    end = parse_datetime_any(end_value)
    if start is None or end is None:
        return 0.0
    try:
        mins = (end - start).total_seconds() / 60.0
        return mins if mins > 0 else 0.0
    except Exception:
        return 0.0


def parse_time_to_minutes(value: Any, default: str = DEFAULT_START) -> int:
    if pd.isna(value) or str(value).strip() == "":
        value = default
    raw_original = str(value).strip()
    raw = raw_original.lower().replace(".", "")

    # BookingKoala exports ISO datetime strings like 2026-05-05T12:00:00-05:00.
    try:
        if "t" in raw and re.search(r"\d{4}-\d{2}-\d{2}", raw):
            dt = datetime.fromisoformat(raw_original.replace("Z", "+00:00"))
            return dt.hour * 60 + dt.minute
    except Exception:
        pass

    # BookingKoala Time column often looks like "09:00 AM - 11:00 AM".
    if " - " in raw_original:
        raw = raw_original.split(" - ", 1)[0].strip().lower().replace(".", "")

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




def parse_duration_to_minutes(value: Any, default: float = 0.0) -> float:
    """Parse BookingKoala-style durations into minutes.

    Handles values like:
    - 3 Hr 53 Min
    - 3h 53m
    - 3:53
    - 233 min
    - 3.88 hours
    Numeric values <= 24 are treated as hours; larger numeric values are treated as minutes.
    """
    if pd.isna(value) or str(value).strip() == "":
        return float(default)
    raw = str(value).strip().lower()
    raw = raw.replace("hours", "hr").replace("hour", "hr").replace("hrs", "hr")
    raw = raw.replace("minutes", "min").replace("minute", "min").replace("mins", "min")
    raw = raw.replace(" ", " ")

    # 3:53 means 3 hours 53 minutes. 00:45 means 45 minutes.
    m = re.match(r"^\s*(\d{1,2})\s*:\s*(\d{2})\s*$", raw)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))

    hr_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:hr|h)\b", raw)
    min_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:min|m)\b", raw)
    if hr_match or min_match:
        hours = float(hr_match.group(1)) if hr_match else 0.0
        mins = float(min_match.group(1)) if min_match else 0.0
        return hours * 60 + mins

    # Phrases like "3 hr 53" after export cleanup.
    parts = re.findall(r"\d+(?:\.\d+)?", raw)
    if len(parts) >= 2 and any(tok in raw for tok in ["hr", "h"]):
        return float(parts[0]) * 60 + float(parts[1])

    val = parse_float(raw, default)
    if val <= 0:
        return float(default)
    return val if val > 24 else val * 60


def parse_worker_count(*values: Any, default: int = 1) -> int:
    """Infer cleaner count from numeric or name-list fields."""
    for value in values:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        raw = str(value).strip()
        if not raw:
            continue
        nums = re.findall(r"\d+", raw)
        if nums and any(w in raw.lower() for w in ["cleaner", "cleaners", "staff", "worker", "workers", "maid", "maids", "person", "people"]):
            return max(1, int(nums[0]))
        if re.fullmatch(r"\d+(?:\.0)?", raw):
            return max(1, int(float(raw)))
        # Name lists: Isabel, Jacky OR Isabel/Jacky OR Billy + Eduardo.
        # BookingKoala provider lists include IDs like "6460: Billy Taylor, 6569: Eduardo Lopez".
        raw_names = re.sub(r"\b\d+\s*:\s*", "", raw)
        if any(sep in raw_names for sep in [",", ";", "|", "+", "&", "/"]):
            cleaned = re.sub(r"\band\b", ",", raw_names, flags=re.I)
            names = [x.strip() for x in re.split(r"[,;|+&/]+", cleaned) if x.strip()]
            # Ignore values that look like addresses or dates.
            if 1 < len(names) <= 6 and not any(re.search(r"\d{3,}", n) for n in names):
                return len(names)
    return max(1, int(default))


def looks_like_time(value: Any) -> bool:
    if pd.isna(value) or str(value).strip() == "":
        return False
    raw = str(value).strip().lower()
    if "t" in raw and re.search(r"\d{4}-\d{2}-\d{2}", raw):
        return True
    return bool(re.search(r"\d{1,2}\s*:?\s*\d{0,2}\s*(am|pm)\b", raw) or re.fullmatch(r"\d{1,2}:\d{2}", raw))


def add_minutes_to_time_string(value: Any, minutes: float) -> str:
    if not looks_like_time(value):
        return ""
    start = parse_time_to_minutes(value, DEFAULT_START)
    return minutes_to_time(start + minutes)

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
    "customer": "client", "customer_name": "client", "full_name": "client", "name": "client", "client_name": "client",
    "service_address": "address", "client_address": "address", "home_address": "address", "location": "address", "full_address": "address",
    "date": "service_date", "booking_date": "service_date", "appointment_date": "service_date", "cleaning_date": "service_date", "service_date_time": "service_date",
    "day": "preferred_day", "requested_day": "preferred_day", "preferred_date": "service_date",
    "service": "cleaning_type", "service_type": "cleaning_type", "type": "cleaning_type", "booking_type": "cleaning_type",

    # Manual/person-hour fields
    "hours": "job_hours", "estimated_hours": "job_hours", "person_hours": "job_hours", "job_hours": "job_hours",

    # BookingKoala appointment duration fields. These are usually real clock duration for the booked team.
    "duration": "appointment_duration", "job_duration": "appointment_duration", "appointment_duration": "appointment_duration",
    "service_duration": "appointment_duration", "booking_duration": "appointment_duration", "estimated_duration": "appointment_duration",
    "total_duration": "appointment_duration", "duration_hours": "appointment_duration", "duration_hrs": "appointment_duration",
    "duration_minutes": "appointment_duration", "duration_mins": "appointment_duration", "duration_min": "appointment_duration",

    # Start/end time fields from BookingKoala exports.
    "time": "booking_time", "booking_time": "booking_time", "appointment_time": "booking_time", "scheduled_time": "booking_time", "service_time": "booking_time",
    "booking_start_date_time": "booking_start_datetime", "booking_end_date_time": "booking_end_datetime",
    "estimated_job_length_hh_mm": "bookingkoala_estimated_length",
    "start": "earliest_start", "start_time": "earliest_start", "appointment_start": "earliest_start", "service_start": "earliest_start", "scheduled_start": "earliest_start", "booking_start": "earliest_start", "arrival_start": "earliest_start",
    "end": "latest_finish", "end_time": "latest_finish", "appointment_end": "latest_finish", "service_end": "latest_finish", "scheduled_end": "latest_finish", "booking_end": "latest_finish", "arrival_end": "latest_finish",
    "preferred_time": "time_window", "arrival_window": "time_window", "time_window": "time_window",

    "can_move": "can_shift", "flexible": "can_shift", "shiftable": "can_shift",
    "booking_note": "notes", "private_customer_note": "private_customer_notes", "provider_note": "provider_notes", "booking_id": "booking_id",
    "status": "booking_status", "booking_status": "booking_status", "lead_status": "booking_status", "hold_status": "booking_status",
    "hold_id": "hold_id", "live_booking_id": "hold_id", "source": "booking_source", "booking_source": "booking_source",
    "price": "job_price", "quote": "job_price", "quoted_price": "job_price", "amount": "job_price", "total": "job_price", "total_price": "job_price", "booking_total": "job_price",
    "final_amount_usd": "job_price", "service_total_usd": "service_total", "price_adjustment": "price_adjustment",

    # Current/required cleaner information in BookingKoala exports.
    "assigned_cleaner": "assigned_workers", "assigned_cleaners": "assigned_workers", "assigned_staff": "assigned_workers", "assigned_team": "assigned_workers",
    "provider": "assigned_workers", "providers": "assigned_workers", "provider_team": "assigned_workers", "staff": "assigned_workers", "team": "assigned_workers", "maids": "assigned_workers", "cleaners": "assigned_workers",
    "number_of_cleaners": "original_worker_count", "cleaner_count": "original_worker_count", "cleaners_count": "original_worker_count",
    "staff_count": "original_worker_count", "worker_count": "original_worker_count", "workers": "original_worker_count", "team_size": "original_worker_count", "number_of_maids": "original_worker_count",

    "regular_cleaner": "preferred_resource", "preferred_cleaner": "preferred_resource", "preferred_team": "preferred_resource", "preferred_resource": "preferred_resource",
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

    optional_cols = [
        "service_date", "preferred_day", "flexible_days", "time_window", "booking_time", "cleaning_type", "can_shift",
        "notes", "earliest_start", "latest_finish", "frequency", "preferred_resource", "lock_resource",
        "job_price", "risk_level", "difficulty_level", "buffer_minutes", "priority", "min_workers", "max_workers",
        "requires_team", "recurrence_interval_weeks", "client_flexibility", "job_hours", "appointment_duration",
        "booking_start_datetime", "booking_end_datetime", "bookingkoala_estimated_length", "service_total",
        "original_worker_count", "assigned_workers", "booking_status", "hold_id", "booking_source",
    ]
    for col in optional_cols:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")

    df["client"] = df["client"].astype(str).str.strip()
    df["client_key"] = df["client"].apply(key)
    df["address"] = df["address"].astype(str).str.strip()
    df["city"] = df["address"].apply(extract_city)

    # Exact BookingKoala export support:
    # - Time is the arrival window (ex: 12:00 PM - 02:00 PM), not the job length.
    # - Booking start/end is useful as the original calendar block.
    # - Estimated job length (HH:MM) is treated as TOTAL ONE-PERSON LABOR TIME.
    #   Simple rule requested by Dynamic Duo Cleaning:
    #     1 assigned cleaner  -> duration = total labor time
    #     2 assigned cleaners -> duration = total labor time / 2
    #     3 assigned cleaners -> duration = total labor time / 3
    for idx, row in df.iterrows():
        start_dt = parse_datetime_any(row.get("booking_start_datetime", ""))
        end_dt = parse_datetime_any(row.get("booking_end_datetime", ""))
        if start_dt is not None:
            if not str(row.get("service_date", "")).strip():
                df.at[idx, "service_date"] = start_dt.date().isoformat()
            # Use the scheduled start as the starting point when BookingKoala provides it.
            df.at[idx, "earliest_start"] = row.get("booking_start_datetime", "")
        if end_dt is not None:
            # Keep original end for reference/debugging. The optimizer still recalculates actual duration
            # from total labor time / assigned cleaner count.
            df.at[idx, "latest_finish"] = row.get("booking_end_datetime", "")
        if start_dt is not None and not str(row.get("time_window", "")).strip():
            df.at[idx, "time_window"] = "Fixed"

    # BookingKoala duration logic:
    # Treat Estimated job length (HH:MM) as total one-person labor minutes, then divide by assigned team size later.
    # Example: 07:45 total labor
    #   - 1 cleaner  = 7h45m
    #   - 2 cleaners = 3h53m
    #   - 3 cleaners = 2h35m
    appointment_mins: List[float] = []
    original_workers: List[int] = []
    person_mins: List[int] = []
    duration_sources: List[str] = []
    for _, row in df.iterrows():
        block_mins = datetime_diff_minutes(row.get("booking_start_datetime", ""), row.get("booking_end_datetime", ""))
        estimated_length_mins = parse_duration_to_minutes(row.get("bookingkoala_estimated_length", ""), 0.0)
        explicit_appt_mins = parse_duration_to_minutes(row.get("appointment_duration", ""), 0.0)
        workers = parse_worker_count(row.get("original_worker_count", ""), row.get("assigned_workers", ""), default=1)
        manual_mins = parse_duration_to_minutes(row.get("job_hours", ""), 0.0)
        workers = max(1, int(workers))

        if estimated_length_mins > 0:
            # Main BookingKoala rule: the CSV labor time is already total one-person labor.
            base_person = int(round(estimated_length_mins))
            appt = base_person / workers
            source = f"BookingKoala Estimated job length as total labor ({round(base_person)} min ÷ {workers} cleaner(s))"
            if block_mins > 0:
                source += f"; original calendar block {round(block_mins)} min"
        elif explicit_appt_mins > 0:
            # Fallback if a duration exists but no Estimated job length column/value exists.
            appt = explicit_appt_mins
            base_person = int(round(appt * workers))
            source = f"Fallback appointment duration × cleaners ({round(appt)} min × {workers})"
        elif block_mins > 0:
            # Last BookingKoala fallback: original calendar block × provider count.
            appt = block_mins
            base_person = int(round(appt * workers))
            source = f"Fallback start/end block × cleaners ({round(appt)} min × {workers})"
        elif manual_mins > 0:
            appt = 0.0
            base_person = int(round(manual_mins))
            source = "Manual job_hours/person-hours"
        else:
            appt = 0.0
            base_person = 150
            source = "Default 2.5 person-hours"
        appointment_mins.append(float(appt))
        original_workers.append(int(workers))
        person_mins.append(max(15, int(base_person)))
        duration_sources.append(source)

    df["bookingkoala_duration_mins"] = appointment_mins
    df["bookingkoala_worker_count"] = original_workers
    df["job_mins"] = person_mins
    df["job_hours"] = (df["job_mins"] / 60).round(2)
    df["duration_source"] = duration_sources

    # If BookingKoala has a real scheduled time, use it instead of creating our own random time.
    # Use the time as fixed unless the row clearly says it is flexible/can shift.
    for idx, row in df.iterrows():
        if (not str(row.get("earliest_start", "")).strip()) and looks_like_time(row.get("booking_time", "")):
            df.at[idx, "earliest_start"] = row.get("booking_time", "")
        # Do not auto-create latest_finish from duration. BookingKoala duration is a work block, not always a hard client latest finish.
        # If the export has a real end_time/latest_finish column, it will still be respected.
        if looks_like_time(df.at[idx, "earliest_start"]) and not str(row.get("time_window", "")).strip():
            df.at[idx, "time_window"] = "Fixed"

    # BookingKoala exports both Service total and Final amount. Use Final amount when present; otherwise Service total.
    if "job_price" in df.columns and "service_total" in df.columns:
        df["job_price"] = df.apply(lambda r: r.get("job_price") if str(r.get("job_price", "")).strip() else r.get("service_total", ""), axis=1)
    df["job_price_num"] = df["job_price"].apply(lambda x: max(0.0, parse_float(x, 0.0)))
    df["service_date_parsed"] = df["service_date"].apply(parse_date)
    df["preferred_day_list"] = df["preferred_day"].apply(split_days)
    df["flexible_day_list"] = df["flexible_days"].apply(split_days)
    df["can_shift_bool"] = df["can_shift"].apply(truthy)
    df["lock_resource_bool"] = df["lock_resource"].apply(truthy)
    df["preferred_resource_key"] = df["preferred_resource"].apply(key)
    df["assigned_workers_key"] = df["assigned_workers"].apply(key)
    df["min_workers_num"] = df["min_workers"].apply(lambda x: max(1, parse_int(x, 1)))
    # original_worker_count is used to calculate person-time, not to force the same team size.
    # Use min_workers/requires_team if a job truly must have multiple cleaners.
    df["max_workers_num"] = df["max_workers"].apply(lambda x: max(1, parse_int(x, 99)))
    df["requires_team_bool"] = df["requires_team"].apply(truthy)
    df.loc[df["bookingkoala_worker_count"] > 1, "requires_team_bool"] = df.loc[df["bookingkoala_worker_count"] > 1, "requires_team_bool"].fillna(False)
    df["priority_rank"] = df["priority"].apply(priority_rank)
    risk_buffers = df.apply(calculate_risk_buffer_minutes, axis=1)
    df["buffer_mins"] = [x[0] for x in risk_buffers]
    df["buffer_source"] = [x[1] for x in risk_buffers]
    windows = df.apply(infer_time_window, axis=1)
    df["earliest_min"] = [x[0] for x in windows]
    df["latest_finish_min"] = [x[1] for x in windows]
    df["time_window_label"] = [x[2] for x in windows]
    df["fixed_time_bool"] = [x[3] for x in windows]
    df["scheduled_start_min"] = df["earliest_start"].apply(lambda x: parse_time_to_minutes(x, DEFAULT_START) if looks_like_time(x) else math.nan)
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



def generate_smart_temp_pair_crews(cleaners_raw: pd.DataFrame, crews_raw: Optional[pd.DataFrame], days: List[date], max_pairs: int = 12) -> pd.DataFrame:
    """Build optional two-person teams for the optimizer.

    This does not force two cleaners on every job. It simply gives the optimizer
    legal team options so it can compare solo vs pair for large jobs. Fixed crews
    and cleaners marked allow_solo=No are not mixed into random temporary pairs.
    """
    if cleaners_raw is None or cleaners_raw.empty:
        return pd.DataFrame(columns=["resource_name", "members", "team_type", "available_days", "base_address", "can_split_after_job", "always_together", "carpool", "max_jobs_per_day", "max_hours_per_day", "start_time", "end_time", "productivity_multiplier", "hourly_cost_override"])
    cleaners = prepare_cleaners(cleaners_raw)
    try:
        crews = prepare_crew_rules(crews_raw) if crews_raw is not None and not crews_raw.empty else pd.DataFrame()
    except Exception:
        crews = pd.DataFrame()

    fixed_member_keys = set()
    existing_resource_keys = set()
    if crews is not None and not crews.empty:
        existing_resource_keys = set(crews.get("resource_key", pd.Series(dtype=str)).astype(str))
        for _, cr in crews.iterrows():
            if bool(cr.get("always_together_bool", False)):
                fixed_member_keys.update(cr.get("member_keys", []))

    candidates = cleaners[(cleaners["allow_solo_bool"] == True) & (~cleaners["cleaner_key"].isin(fixed_member_keys))].copy()
    if len(candidates) < 2:
        return pd.DataFrame()

    # Respect can_pair_with if provided; otherwise allow normal pair options.
    rows = []
    day_names = sorted({DAY_LABEL[date_to_day(d)] for d in days}) or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    records = candidates.to_dict("records")
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            a, b = records[i], records[j]
            a_pairs = [key(x) for x in split_list(a.get("can_pair_with", ""))]
            b_pairs = [key(x) for x in split_list(b.get("can_pair_with", ""))]
            if a_pairs and b["cleaner_key"] not in a_pairs:
                continue
            if b_pairs and a["cleaner_key"] not in b_pairs:
                continue
            name = f"{a['cleaner']}/{b['cleaner']}"
            rkey = key(name)
            if rkey in existing_resource_keys:
                continue
            hourly = float(a.get("hourly_cost", 25.0)) + float(b.get("hourly_cost", 25.0))
            # Use first cleaner base for the starting point. Split/join still gets flagged for review.
            rows.append({
                "resource_name": name,
                "members": f"{a['cleaner']};{b['cleaner']}",
                "team_type": "Smart Optional",
                "available_days": ",".join(day_names),
                "base_address": str(a.get("base_address", "")),
                "can_split_after_job": "Yes",
                "always_together": "No",
                "carpool": "Yes",
                "max_jobs_per_day": min(int(a.get("max_jobs_per_day", 3)), int(b.get("max_jobs_per_day", 3)), 4),
                "max_hours_per_day": min(float(a.get("max_hours_per_day", 8)), float(b.get("max_hours_per_day", 8))),
                "start_time": DEFAULT_START,
                "end_time": DEFAULT_END,
                "productivity_multiplier": 2,
                "hourly_cost_override": hourly,
            })
            if len(rows) >= int(max_pairs):
                return pd.DataFrame(rows)
    return pd.DataFrame(rows)

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


def approximate_route_matrix(points: List[Dict[str, Any]]) -> Tuple[List[List[float]], List[List[float]]]:
    n = len(points)
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
    return miles, minutes


def response_error_summary(resp: requests.Response) -> str:
    body = ""
    try:
        body = resp.text.strip().replace("\n", " ")
    except Exception:
        body = ""
    if len(body) > 500:
        body = body[:500] + "..."
    return f"HTTP {resp.status_code}: {body}" if body else f"HTTP {resp.status_code}"


def compute_routes_api_matrix(points: List[Dict[str, Any]], api_key: str, chunk_size: int = 20) -> Tuple[List[List[float]], List[List[float]]]:
    n = len(points)
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
            if not resp.ok:
                raise RuntimeError(response_error_summary(resp))
            data = resp.json()
            for entry in data:
                src = oi + int(entry.get("originIndex", 0))
                dst = di + int(entry.get("destinationIndex", 0))
                if src == dst:
                    continue
                if entry.get("condition") == "ROUTE_EXISTS" or entry.get("distanceMeters") is not None:
                    miles[src][dst] = float(entry.get("distanceMeters", 0)) * MILES_PER_METER
                    minutes[src][dst] = duration_to_minutes(entry.get("duration"))
    return miles, minutes


def compute_legacy_distance_matrix(points: List[Dict[str, Any]], api_key: str, chunk_size: int = 10) -> Tuple[List[List[float]], List[List[float]]]:
    # Backup provider. Requires Distance Matrix API (Legacy) enabled in the same Google Cloud project.
    n = len(points)
    miles = [[0.0 if i == j else math.inf for j in range(n)] for i in range(n)]
    minutes = [[0.0 if i == j else math.inf for j in range(n)] for i in range(n)]
    url = "https://maps.googleapis.com/maps/api/distancematrix/json"

    def pt(p: Dict[str, Any]) -> str:
        return f'{float(p["lat"]):.7f},{float(p["lng"]):.7f}'

    for oi in range(0, n, chunk_size):
        o_chunk = points[oi: oi + chunk_size]
        for di in range(0, n, chunk_size):
            d_chunk = points[di: di + chunk_size]
            params = {
                "origins": "|".join(pt(p) for p in o_chunk),
                "destinations": "|".join(pt(p) for p in d_chunk),
                "mode": "driving",
                "units": "imperial",
                "key": api_key,
            }
            resp = requests.get(url, params=params, timeout=60)
            if not resp.ok:
                raise RuntimeError(response_error_summary(resp))
            data = resp.json()
            status = data.get("status")
            if status != "OK":
                raise RuntimeError(f"Distance Matrix API status {status}: {data.get('error_message', '')}")
            for r_idx, row in enumerate(data.get("rows", [])):
                for c_idx, element in enumerate(row.get("elements", [])):
                    src = oi + r_idx
                    dst = di + c_idx
                    if src == dst:
                        continue
                    if element.get("status") == "OK":
                        miles[src][dst] = float(element["distance"].get("value", 0)) * MILES_PER_METER
                        minutes[src][dst] = float(element["duration"].get("value", 0)) / 60.0
    return miles, minutes


@st.cache_data(show_spinner=False, ttl=60 * 60 * 8)
def compute_route_matrix(points: List[Dict[str, Any]], api_key: str, use_google: bool, provider: str = "Auto", chunk_size: int = 20) -> Tuple[List[List[float]], List[List[float]], str]:
    """Return driving matrix and a compact source label.

    v11 keeps the interface clean: if Google rejects the key, we do not dump the full
    error into the main app. The detailed reason is stored in session_state for the
    Google routing help expander.
    """
    if not use_google or not api_key or provider == "Approximate only":
        m, t = approximate_route_matrix(points)
        try:
            st.session_state["last_google_routing_error"] = ""
        except Exception:
            pass
        return m, t, "Approximate fallback"

    provider = provider or "Auto"
    routes_error = ""
    legacy_error = ""

    if provider in ["Auto", "Routes API"]:
        try:
            m, t = compute_routes_api_matrix(points, api_key, chunk_size=chunk_size)
            try:
                st.session_state["last_google_routing_error"] = ""
            except Exception:
                pass
            return m, t, "Google Routes API"
        except Exception as exc:
            routes_error = str(exc)

    if provider in ["Auto", "Distance Matrix API (Legacy)"]:
        try:
            m, t = compute_legacy_distance_matrix(points, api_key, chunk_size=10)
            try:
                st.session_state["last_google_routing_error"] = ""
            except Exception:
                pass
            return m, t, "Google Distance Matrix API (Legacy)"
        except Exception as exc:
            legacy_error = str(exc)

    details = []
    if routes_error:
        details.append(f"Routes API: {routes_error}")
    if legacy_error:
        details.append(f"Distance Matrix API: {legacy_error}")
    details_txt = " | ".join(details) or "Unknown Google Maps error"
    try:
        st.session_state["last_google_routing_error"] = details_txt
    except Exception:
        pass

    # Compact label shown in the schedule. Detailed setup help lives in the app UI.
    label = "Approximate fallback — Google routing key blocked"
    if "REQUEST_DENIED" in details_txt or "not authorized" in details_txt:
        label = "Approximate fallback — API key not authorized"
    elif "API_KEY_SERVICE_BLOCKED" in details_txt or "blocked" in details_txt.lower():
        label = "Approximate fallback — API service blocked on key"

    m, t = approximate_route_matrix(points)
    return m, t, label

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
            "team_type": crew.get("team_type", "Optional"),
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
            "team_type": "Solo",
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


def _event_parts(event: Tuple[Any, ...]) -> Tuple[int, int, str, str]:
    s = int(event[0]) if len(event) > 0 else 0
    e = int(event[1]) if len(event) > 1 else 0
    label = str(event[2]) if len(event) > 2 else "existing job"
    point_key = str(event[3]) if len(event) > 3 else ""
    return s, e, label, point_key


def member_conflict(member_events: Dict[Tuple[str, date], List[Tuple[Any, ...]]], member_keys: List[str], d: date, start: int, end: int) -> Optional[str]:
    for m in member_keys:
        for ev in member_events.get((m, d), []):
            s, e, label, _ = _event_parts(ev)
            if interval_overlaps(start, end, s, e):
                return f"{m} overlaps with {label}"
    return None


def member_travel_conflict(member_events: Dict[Tuple[str, date], List[Tuple[Any, ...]]], member_keys: List[str], d: date, start: int, end: int, job_key: str, minutes: List[List[float]], point_idx: Dict[str, int], min_gap_minutes: int) -> Optional[str]:
    """Ensure split/recombine team jobs have real travel gap for each member.

    This prevents impossible schedules like a cleaner ending one solo job at 11:30
    and instantly appearing on a paired job at 11:30 somewhere else.
    """
    if job_key not in point_idx:
        return None
    for m in member_keys:
        for ev in member_events.get((m, d), []):
            s, e, label, prev_point = _event_parts(ev)
            if prev_point not in point_idx:
                continue
            if e <= start:
                drive = minutes[point_idx[prev_point]][point_idx[job_key]]
                if not math.isinf(drive) and e + int(min_gap_minutes) + int(math.ceil(drive)) > start:
                    return f"{m} cannot reach from {label} with travel + {min_gap_minutes} min gap"
            elif s >= end:
                drive = minutes[point_idx[job_key]][point_idx[prev_point]]
                if not math.isinf(drive) and end + int(min_gap_minutes) + int(math.ceil(drive)) > s:
                    return f"{m} cannot reach next job {label} with travel + {min_gap_minutes} min gap"
    return None


def actual_job_minutes(row: pd.Series, resource: Dict[str, Any], hours_are_person_hours: bool) -> int:
    base = int(row.get("job_mins", 150))
    buffer_mins = int(row.get("buffer_mins", 0))
    effective_workers = max(1.0, float(resource.get("productivity_multiplier", resource.get("member_count", 1))))

    # BookingKoala appointment durations are converted into person-minutes in prepare_bookings.
    # Those should always be divided by the assigned team productivity.
    from_bookingkoala = float(row.get("bookingkoala_duration_mins", 0) or 0) > 0 or "bookingkoala" in str(row.get("duration_source", "")).lower()
    if from_bookingkoala or hours_are_person_hours:
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


def evaluate_candidate(row: pd.Series, resource: Dict[str, Any], d: date, schedules: Dict[Tuple[str, date], List[Dict[str, Any]]], member_events: Dict[Tuple[str, date], List[Tuple[int, int, str]]], exceptions: pd.DataFrame, miles: List[List[float]], minutes: List[List[float]], point_idx: Dict[str, int], area_memory: pd.DataFrame, hours_are_person_hours: bool, mileage_cost: float, travel_hour_cost: float, min_gap_minutes: int = DEFAULT_MIN_GAP_MINUTES) -> Tuple[bool, Dict[str, Any]]:
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
    # Mileage policy for Dynamic Duo:
    # - Do NOT count home/base -> first job as route mileage.
    # - Do NOT count last job -> home/base.
    # - Only count job -> job travel inside the workday.
    # We still look at base -> first job as a light positioning signal so the
    # first job is not assigned to a completely unreasonable cleaner, but it is
    # never included in the displayed/paid route miles or travel cost.
    route_is_empty = len(route) == 0
    last_key = route[-1]["point_key"] if route else resource_base_key
    raw_miles = miles[point_idx[last_key]][point_idx[job_key]]
    raw_minutes = minutes[point_idx[last_key]][point_idx[job_key]]
    if math.isinf(raw_miles) or math.isinf(raw_minutes):
        return False, {"reason": "No route data"}
    positioning_miles = float(raw_miles) if route_is_empty else 0.0
    positioning_minutes = float(raw_minutes) if route_is_empty else 0.0
    travel_miles = 0.0 if route_is_empty else float(raw_miles)
    travel_minutes = 0.0 if route_is_empty else float(raw_minutes)
    prev_end = route[-1]["end_min"] if route else avail_start
    gap_after_prev = int(min_gap_minutes) if route else 0
    arrival = prev_end + gap_after_prev + int(math.ceil(travel_minutes))
    duration = actual_job_minutes(row, resource, hours_are_person_hours)

    scheduled_start = row.get("scheduled_start_min", math.nan)
    if bool(row.get("fixed_time_bool")) and not pd.isna(scheduled_start):
        start = max(int(scheduled_start), avail_start)
        # For the first job, assume the cleaner leaves base early enough to arrive for the fixed BookingKoala time.
        # For later jobs, enforce travel + 30-minute gap before the fixed start.
        if route and arrival > start:
            return False, {"reason": "Cannot reach fixed BookingKoala start time with travel/gap"}
    else:
        start = max(int(row.get("earliest_min", avail_start)), arrival, avail_start)
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
    travel_conflict = member_travel_conflict(member_events, list(resource.get("member_keys", [])), d, start, end, job_key, minutes, point_idx, min_gap_minutes)
    if travel_conflict:
        return False, {"reason": travel_conflict}

    area_adj, area_reason = area_memory_adjustment(row, resource, d, area_memory)
    pref_bonus = 0.0
    if pref_key and not bool(row.get("lock_resource_bool")):
        allowed_keys = [resource["resource_key"]] + list(resource.get("member_keys", []))
        if pref_key in allowed_keys:
            pref_bonus -= 12
    shift_penalty = 0 if d == row.get("candidate_anchor_date") else 18
    priority_penalty = float(row.get("priority_rank", 2)) * 1.0
    team_penalty = 0.0
    person_hours = float(row.get("job_mins", 0) or 0) / 60.0
    member_count = int(resource.get("member_count", 1) or 1)
    if resource.get("resource_type") == "Crew" and not bool(row.get("requires_team_bool")) and int(row.get("min_workers_num", 1)) <= 1:
        # Smart solo/team rule:
        # - small jobs should normally stay solo unless the pair is extremely close
        # - larger jobs can use a pair because it shortens the appointment block
        if person_hours <= 2.25:
            team_penalty += 14
        elif person_hours <= 3.5:
            team_penalty += 6
        elif person_hours >= 7.0 and member_count >= 2:
            team_penalty -= 14
        elif person_hours >= 5.0 and member_count >= 2:
            team_penalty -= 8
        elif person_hours >= 4.0 and member_count >= 2:
            team_penalty -= 4
    # Cluster-first scoring:
    # 1) For the first job on a route, base distance is only a soft positioning signal.
    # 2) For later jobs, job-to-job mileage is the strongest signal.
    # 3) Creating a brand-new route gets a small penalty so nearby jobs are grouped
    #    instead of being spread across every cleaner.
    # 4) Bad long job-to-job jumps are penalized heavily.
    empty_route_penalty = 5.0 if route_is_empty else 0.0
    positioning_penalty = min(positioning_miles * 0.25, 12.0) if route_is_empty else 0.0
    long_jump_penalty = 0.0
    if not route_is_empty:
        if travel_miles >= 30:
            long_jump_penalty += 45
        elif travel_miles >= 22:
            long_jump_penalty += 28
        elif travel_miles >= 15:
            long_jump_penalty += 14
        elif travel_miles <= 5:
            long_jump_penalty -= 5
    score = (
        travel_miles * 1.8
        + (travel_minutes / 6)
        + empty_route_penalty
        + positioning_penalty
        + long_jump_penalty
        + shift_penalty
        + priority_penalty
        + pref_bonus
        + area_adj
        + team_penalty
    )
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
        "positioning_miles_not_counted": round(positioning_miles, 1),
        "positioning_minutes_not_counted": round(positioning_minutes, 0),
        "mileage_policy": "First and last commute excluded; only job-to-job miles count",
        "gap_after_prev_mins": gap_after_prev,
        "bookingkoala_duration_mins": round(float(row.get("bookingkoala_duration_mins", 0) or 0), 0),
        "bookingkoala_worker_count": int(row.get("bookingkoala_worker_count", 1) or 1),
        "assigned_worker_count": int(resource.get("member_count", 1) or 1),
        "team_decision": ("Team" if int(resource.get("member_count", 1) or 1) >= 2 else "Solo"),
        "original_person_hours": round(float(row.get("job_mins", 0) or 0) / 60, 2),
        "duration_source": row.get("duration_source", ""),
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


def optimize_schedule(bookings_inst: pd.DataFrame, resources: pd.DataFrame, dates: List[date], exceptions: pd.DataFrame, miles: List[List[float]], minutes: List[List[float]], point_idx: Dict[str, int], area_memory: pd.DataFrame, hours_are_person_hours: bool, mileage_cost: float, travel_hour_cost: float, min_gap_minutes: int = DEFAULT_MIN_GAP_MINUTES) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[Tuple[str, date], List[Dict[str, Any]]], Dict[Tuple[str, date], List[Tuple[int, int, str]]]]:
    schedules: Dict[Tuple[str, date], List[Dict[str, Any]]] = {}
    member_events: Dict[Tuple[str, date], List[Tuple[int, int, str]]] = {}
    assigned: List[Dict[str, Any]] = []
    unassigned: List[Dict[str, Any]] = []

    if bookings_inst.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), schedules, member_events

    sort_df = bookings_inst.copy()
    sort_df["fixed_sort"] = sort_df["fixed_time_bool"].apply(lambda x: 0 if x else 1)
    sort_df["lock_sort"] = sort_df["lock_resource_bool"].apply(lambda x: 0 if x else 1)
    # Process day-by-day and keep cities loosely together. This is still fast,
    # but behaves more like a dispatcher: fixed/locked/high-priority jobs are
    # placed first, then flexible jobs are pulled into existing clusters.
    sort_df["city_sort"] = sort_df.get("city", "").astype(str).str.lower()
    sort_df = sort_df.sort_values(
        ["candidate_anchor_date", "priority_rank", "fixed_sort", "lock_sort", "city_sort", "earliest_min", "job_mins"],
        ascending=[True, True, True, True, True, True, False],
    )

    for _, row in sort_df.iterrows():
        best: Optional[Dict[str, Any]] = None
        best_fail_reasons: List[str] = []
        cdates = candidate_dates_for_booking(row, dates)
        for d in cdates:
            for _, res_row in resources.iterrows():
                resource = res_row.to_dict()
                ok, cand = evaluate_candidate(row, resource, d, schedules, member_events, exceptions, miles, minutes, point_idx, area_memory, hours_are_person_hours, mileage_cost, travel_hour_cost, min_gap_minutes)
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
                member_events.setdefault((m, best["date"]), []).append((best["start_min"], best["end_min"], str(best["client"]), str(best.get("point_key", ""))))
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


def _member_list_from_value(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(x) for x in value if str(x)]
    if pd.isna(value):
        return []
    raw = str(value)
    raw = raw.strip("[]")
    return [x.strip().strip("'\"") for x in re.split(r"[,;/|]+", raw) if x.strip().strip("'\"")]


def _member_name_lookup(resources: pd.DataFrame) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    if resources is None or resources.empty:
        return lookup
    for _, r in resources.iterrows():
        names = [x.strip() for x in re.split(r"[,;/|]+", str(r.get("members", ""))) if x.strip()]
        keys = r.get("member_keys", [])
        if not isinstance(keys, list):
            keys = _member_list_from_value(keys)
        for idx, mk in enumerate(keys):
            lookup[str(mk)] = names[idx] if idx < len(names) else str(mk).replace("_", " ").title()
    return lookup


def helper_join_suggestions_for_target(target: Dict[str, Any], schedule: pd.DataFrame, resources: pd.DataFrame, miles: List[List[float]], minutes: List[List[float]], point_idx: Dict[str, int], min_gap_minutes: int = DEFAULT_MIN_GAP_MINUTES, max_join_drive_minutes: int = 25, min_time_saved_minutes: int = 30, min_remaining_person_minutes: int = 90) -> pd.DataFrame:
    """Suggest a helper joining an existing/new job after finishing nearby.

    This is advisory only. It does not automatically rewrite the schedule, because
    join-later jobs need human confirmation and BookingKoala may not represent them cleanly.
    """
    if schedule is None or schedule.empty or not target:
        return pd.DataFrame()
    try:
        target_date = target.get("date")
        if isinstance(target_date, pd.Timestamp):
            target_date = target_date.date()
        elif not isinstance(target_date, date):
            target_date = pd.to_datetime(str(target_date)).date()
        target_start = int(target.get("start_min"))
        target_end = int(target.get("end_min"))
        target_point = str(target.get("point_key"))
        target_person_mins = float(target.get("original_person_hours", 0) or 0) * 60.0
        if target_person_mins <= 0:
            target_person_mins = float(target.get("duration_mins", 0) or 0) * max(1, int(target.get("assigned_worker_count", 1) or 1))
        current_members = _member_list_from_value(target.get("member_keys", []))
        current_workers = max(1, len(current_members) or int(target.get("assigned_worker_count", 1) or 1))
        if target_person_mins < 240:  # not worth coordinating a join-later assist for small jobs
            return pd.DataFrame()
    except Exception:
        return pd.DataFrame()

    member_names = _member_name_lookup(resources)
    all_members = sorted(set(member_names.keys()))
    rows: List[Dict[str, Any]] = []
    day_schedule = schedule[schedule["date"].astype(str) == str(target_date)].copy()
    if day_schedule.empty:
        return pd.DataFrame()

    def row_members(rr: pd.Series) -> List[str]:
        return _member_list_from_value(rr.get("member_keys", []))

    for helper in all_members:
        if helper in current_members:
            continue
        helper_rows = day_schedule[day_schedule.apply(lambda rr: helper in row_members(rr), axis=1)].copy()
        if helper_rows.empty:
            continue
        helper_rows = helper_rows.sort_values("end_min")
        # Find jobs the helper finishes while the target job is still in progress.
        prior_options = helper_rows[(helper_rows["end_min"] >= target_start - 15) & (helper_rows["end_min"] <= target_end - 30)].copy()
        for _, prior in prior_options.iterrows():
            if str(prior.get("instance_id", "")) == str(target.get("instance_id", "")):
                continue
            from_key = str(prior.get("point_key", ""))
            if from_key not in point_idx or target_point not in point_idx:
                continue
            drive_min = float(minutes[point_idx[from_key]][point_idx[target_point]])
            drive_mi = float(miles[point_idx[from_key]][point_idx[target_point]])
            if math.isinf(drive_min) or drive_min > max_join_drive_minutes:
                continue
            arrival = int(prior.get("end_min", 0)) + int(min_gap_minutes) + int(math.ceil(drive_min))
            if arrival <= target_start + 15 or arrival >= target_end - 30:
                continue
            completed_person_mins = max(0, arrival - target_start) * current_workers
            remaining_person_mins = target_person_mins - completed_person_mins
            if remaining_person_mins < min_remaining_person_minutes:
                continue
            new_finish = int(math.ceil(arrival + remaining_person_mins / (current_workers + 1)))
            saved = target_end - new_finish
            if saved < min_time_saved_minutes:
                continue
            # Make sure the helper can still reach their next already-scheduled job.
            next_rows = helper_rows[helper_rows["start_min"] > prior.get("end_min", 0)].sort_values("start_min")
            next_note = "No later helper job found"
            feasible_next = True
            if not next_rows.empty:
                nxt = next_rows.iloc[0]
                # If the next job is the target itself, skip to the following one.
                if str(nxt.get("instance_id", "")) == str(target.get("instance_id", "")) and len(next_rows) > 1:
                    nxt = next_rows.iloc[1]
                next_key = str(nxt.get("point_key", ""))
                if next_key in point_idx and target_point in point_idx:
                    back_drive = float(minutes[point_idx[target_point]][point_idx[next_key]])
                    reach_next = new_finish + int(min_gap_minutes) + int(math.ceil(back_drive))
                    feasible_next = reach_next <= int(nxt.get("start_min", 0))
                    next_note = f"Next job: {nxt.get('client','')} at {minutes_to_time(nxt.get('start_min'))}; can reach: {'Yes' if feasible_next else 'No'}"
            if not feasible_next:
                continue
            rows.append({
                "date": target_date,
                "client": target.get("client", ""),
                "current_resource": target.get("resource", ""),
                "helper": member_names.get(helper, helper),
                "helper_after_job": prior.get("client", ""),
                "helper_drive_miles": round(drive_mi, 1),
                "helper_drive_minutes": round(drive_min, 0),
                "helper_arrival": minutes_to_time(arrival),
                "original_finish": minutes_to_time(target_end),
                "estimated_finish_with_helper": minutes_to_time(new_finish),
                "time_saved_minutes": int(round(saved)),
                "recommendation": "Good assist option" if saved >= 45 and drive_min <= 20 else "Possible assist - review",
                "next_job_check": next_note,
            })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["time_saved_minutes", "helper_drive_minutes"], ascending=[False, True]).reset_index(drop=True)


def build_team_assist_suggestions(schedule: pd.DataFrame, resources: pd.DataFrame, miles: List[List[float]], minutes: List[List[float]], point_idx: Dict[str, int], min_gap_minutes: int = DEFAULT_MIN_GAP_MINUTES) -> pd.DataFrame:
    if schedule is None or schedule.empty:
        return pd.DataFrame()
    pieces = []
    # Focus on larger jobs and jobs currently handled solo/small team.
    for _, rr in schedule.iterrows():
        person_hours = float(rr.get("original_person_hours", 0) or 0)
        assigned_workers = int(rr.get("assigned_worker_count", 1) or 1)
        if person_hours < 4.0:
            continue
        if assigned_workers >= 3 and person_hours < 8.0:
            continue
        target = rr.to_dict()
        sug = helper_join_suggestions_for_target(target, schedule, resources, miles, minutes, point_idx, min_gap_minutes=min_gap_minutes)
        if not sug.empty:
            pieces.append(sug)
    if not pieces:
        return pd.DataFrame()
    return pd.concat(pieces, ignore_index=True).drop_duplicates(subset=["date", "client", "helper", "helper_arrival"]).head(25)

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
    """Apply manager review edits back to the full schedule.

    Streamlit's data_editor can return duplicate instance_id rows in some cases
    (for example after sorting/filtering/reruns or duplicated uploaded bookings).
    Pandas requires a unique index for to_dict(orient="index"), so we keep the
    last edited row for each instance_id before building the lookup map.
    """
    if schedule is None or schedule.empty or reviewed_subset is None or reviewed_subset.empty or "instance_id" not in reviewed_subset.columns:
        return add_manager_review_columns(schedule)

    out = add_manager_review_columns(schedule)
    editable_cols = [c for c in ["manager_status", "lock_assignment", "manager_note"] if c in reviewed_subset.columns]
    if not editable_cols:
        return out

    subset = reviewed_subset[["instance_id"] + editable_cols].copy()
    subset["instance_id"] = subset["instance_id"].astype(str)
    subset = subset[subset["instance_id"].notna() & (subset["instance_id"].str.strip() != "")]
    subset = subset.drop_duplicates(subset=["instance_id"], keep="last")

    status_map = subset.set_index("instance_id")[editable_cols].to_dict("index")
    for idx, row in out.iterrows():
        iid = str(row.get("instance_id"))
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
    "live_bookings": "Live Bookings",
    "booking_decisions": "Booking Decisions",
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
        "live_bookings": pd.DataFrame(),
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

    # Live Bookings are the new daily planning layer: pending holds and confirmed
    # bookings created from the New Booking Planner. They are included in the
    # optimizer unless cancelled/rejected, so future suggestions do not ignore
    # bookings you already offered to clients.
    if use_sheets:
        out["live_bookings"], status = read_google_tab(sheet_id, MASTER_SHEET_TABS["live_bookings"], defaults["live_bookings"])
        statuses.append(status)
    else:
        out["live_bookings"] = defaults["live_bookings"]
        statuses.append("Live Bookings: using blank data")
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



def active_live_status(value: Any) -> bool:
    """Whether a live booking should be counted as holding space in future schedules."""
    status = str(value or "").strip().lower()
    if not status:
        return True
    return status not in {"rejected", "reject", "cancelled", "canceled", "lost", "released", "deleted"}


def combine_active_and_live_bookings(active_bookings: pd.DataFrame, live_bookings: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Combine uploaded/active BookingKoala rows with saved live holds/confirmed leads.

    Active Bookings remains the BookingKoala export. Live Bookings is the day-to-day
    decision layer where admins save pending or confirmed new jobs before they are
    fully reflected in BookingKoala.
    """
    parts: List[pd.DataFrame] = []
    if active_bookings is not None and not active_bookings.empty:
        a = active_bookings.copy()
        if "booking_source" not in a.columns:
            a.insert(0, "booking_source", "BookingKoala Active Schedule")
        if "booking_status" not in a.columns:
            a.insert(1, "booking_status", "Confirmed")
        parts.append(a)
    if live_bookings is not None and not live_bookings.empty:
        l = normalize_columns(live_bookings.copy(), BOOKING_ALIASES)
        if "booking_status" not in l.columns:
            l["booking_status"] = "Pending"
        l = l[l["booking_status"].apply(active_live_status)].copy()
        if not l.empty:
            if "booking_source" not in l.columns:
                l.insert(0, "booking_source", "Live Booking Planner")
            parts.append(l)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True, sort=False)


def append_google_tab(sheet_id: str, tab_name: str, row_df: pd.DataFrame) -> str:
    """Append rows to a Google Sheet tab, creating the tab/header if needed."""
    ok, msg = google_sheets_configured(sheet_id)
    if not ok:
        return msg
    existing, _ = read_google_tab(sheet_id, tab_name, pd.DataFrame())
    if existing is None or existing.empty or list(existing.columns) == ["message"]:
        combined = row_df.copy()
    else:
        combined = pd.concat([existing, row_df], ignore_index=True, sort=False)
    return write_google_tab(sheet_id, tab_name, combined)


def build_booking_decision_row(new_row: Dict[str, Any], selected_option: Dict[str, Any], suggestions: pd.DataFrame, decision_status: str, admin_name: str = "Admin") -> pd.DataFrame:
    """Create one audit row showing why a live booking was placed in a slot."""
    top = suggestions.head(5).copy() if suggestions is not None and not suggestions.empty else pd.DataFrame()
    alt_summary = ""
    if not top.empty:
        bits = []
        for _, r in top.iterrows():
            bits.append(f"{r.get('day')} {r.get('start')} {r.get('resource')} {r.get('travel_miles')}mi score {round(float(r.get('score', 0)),1)}")
        alt_summary = " | ".join(bits)
    return pd.DataFrame([{
        "decision_time": datetime.now().isoformat(timespec="seconds"),
        "admin": admin_name or "Admin",
        "decision_status": decision_status,
        "client": new_row.get("client", ""),
        "address": new_row.get("address", ""),
        "chosen_date": selected_option.get("date", ""),
        "chosen_day": selected_option.get("day", ""),
        "chosen_start": selected_option.get("start", ""),
        "chosen_end": selected_option.get("end", ""),
        "chosen_resource": selected_option.get("resource", ""),
        "assigned_worker_count": selected_option.get("assigned_worker_count", ""),
        "travel_miles": selected_option.get("travel_miles", ""),
        "profit_score": selected_option.get("profit_score", ""),
        "reason": selected_option.get("why_this_option", selected_option.get("score_notes", "")),
        "top_options": alt_summary,
    }])

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
