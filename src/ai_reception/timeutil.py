from __future__ import annotations
from datetime import datetime, timedelta

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

def parse_iso(s: str):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def parse_friendly_dt(s: str):
    s = (s or "").strip()
    if not s:
        return None
    s2 = s.replace("/", "-")
    if "T" in s2:
        return parse_iso(s2)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s2, fmt)
        except Exception:
            pass
    return None

def dt_to_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")

def add_minutes(dt: datetime, mins: int) -> datetime:
    return dt + timedelta(minutes=mins)
