from __future__ import annotations
import re
from datetime import datetime

PHONE_RE = re.compile(r"(\+?\d[\d\-\s]{7,}\d)")

def extract_customer_task_fields(text: str, fallback_phone: str = ""):
    t = (text or "").strip()
    phone = fallback_phone
    m = PHONE_RE.search(t)
    if m:
        phone = re.sub(r"\s+", "", m.group(1))

    address = ""
    for kw in ["地址", "位置", "到", "在", "送到"]:
        if kw in t:
            idx = t.find(kw)
            address = t[idx: idx+80]
            break

    title = (t[:18] + "…") if len(t) > 18 else (t if t else "新任务")
    notes = t[:500]
    return {"title": title, "address": address, "contact_phone": phone, "notes": notes}

def detect_leave_request(text: str):
    t = (text or "").strip()
    if not t:
        return None
    # very simple: contains 请假 / 休假 / 病假
    if ("请假" not in t) and ("休假" not in t) and ("病假" not in t):
        return None

    # parse like: 2025-12-31 10:00-18:00
    start, end = None, None
    m = re.search(r"(20\d{2}-\d{1,2}-\d{1,2})\s*(\d{1,2}:\d{2})\s*[-~到]\s*(\d{1,2}:\d{2})", t)
    if m:
        d = m.group(1)
        t1, t2 = m.group(2), m.group(3)
        try:
            start = datetime.strptime(f"{d} {t1}", "%Y-%m-%d %H:%M").isoformat(timespec="seconds")
            end = datetime.strptime(f"{d} {t2}", "%Y-%m-%d %H:%M").isoformat(timespec="seconds")
        except Exception:
            start, end = None, None
    return {"content": t[:500], "start_time": start, "end_time": end}
