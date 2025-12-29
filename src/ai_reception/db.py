from __future__ import annotations
import sqlite3, json
from pathlib import Path
from typing import Any
from .timeutil import now_iso, parse_iso

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS staff(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  phone TEXT NOT NULL UNIQUE,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS conversations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL DEFAULT 'customer' CHECK(kind IN ('customer','staff')),
  display_name TEXT,
  last_message TEXT,
  last_time TEXT
);

CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conv_id INTEGER NOT NULL,
  direction TEXT NOT NULL CHECK(direction IN ('in','out','sys')),
  text TEXT NOT NULL,
  time TEXT NOT NULL,
  meta_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(conv_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_conv_time ON messages(conv_id, time);

CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conv_id INTEGER,
  title TEXT,
  address TEXT,
  contact_phone TEXT,
  start_time TEXT,
  duration_min INTEGER NOT NULL DEFAULT 60,
  staff_id INTEGER,
  status TEXT NOT NULL DEFAULT 'TODO' CHECK(status IN ('TODO','HOLD','CONFIRMED','IN_PROGRESS','DONE','CANCELLED','EXPIRED')),
  hold_expires_at TEXT,
  notes TEXT,
  created_time TEXT NOT NULL,
  updated_time TEXT NOT NULL,
  FOREIGN KEY(conv_id) REFERENCES conversations(id) ON DELETE SET NULL,
  FOREIGN KEY(staff_id) REFERENCES staff(id) ON DELETE SET NULL
);

-- guard: same staff + same start time
CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_staff_start_active
ON tasks(staff_id, start_time)
WHERE staff_id IS NOT NULL AND start_time IS NOT NULL AND status IN ('HOLD','CONFIRMED','IN_PROGRESS');

CREATE TABLE IF NOT EXISTS staff_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  staff_id INTEGER NOT NULL,
  type TEXT NOT NULL CHECK(type IN ('leave')),
  content TEXT NOT NULL,
  start_time TEXT,
  end_time TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','APPROVED','REJECTED')),
  created_time TEXT NOT NULL,
  updated_time TEXT NOT NULL,
  FOREIGN KEY(staff_id) REFERENCES staff(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS kb_entries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  version INTEGER NOT NULL DEFAULT 1,
  updated_time TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "lang": "zh",
    "sms_mode": "simulator",         # simulator|off (future: android_bridge)
    "llm_mode": "local_first",       # local_first|cloud_first|off
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "llama3.1:8b",
    "cloud_base_url": "https://api.openai.com",
    "cloud_api_key": "",
    "cloud_model": "gpt-4o-mini",
    "hold_minutes": "10",
}

class DB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self._init()

    def close(self):
        try: self.conn.close()
        except Exception: pass

    def _init(self):
        cur = self.conn.cursor()
        cur.executescript(SCHEMA)
        self.conn.commit()
        for k,v in DEFAULT_SETTINGS.items():
            cur.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k,v))
        self.conn.commit()

        # seed staff
        if self.conn.execute("SELECT COUNT(*) c FROM staff").fetchone()["c"] == 0:
            self.conn.executemany(
                "INSERT INTO staff(name,phone,active) VALUES(?,?,1)",
                [("员工A","0211111111"), ("员工B","0222222222"), ("员工C","0233333333")]
            )
            self.conn.commit()

        # seed KB
        if self.conn.execute("SELECT COUNT(*) c FROM kb_entries").fetchone()["c"] == 0:
            self.conn.executemany(
                "INSERT INTO kb_entries(title,content,tags,enabled,version,updated_time) VALUES(?,?,?,?,?,?)",
                [
                    ("欢迎语", "你好，我是接待。请发：时间、地址、联系电话、以及具体要求。", "话术", 1, 1, now_iso()),
                    ("请假格式", "员工请假短信建议：请假 2025-12-31 10:00-18:00 原因：xxx", "内部", 1, 1, now_iso()),
                ]
            )
            self.conn.commit()

    # settings
    def get_setting(self, key: str) -> str:
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else ""

    def set_setting(self, key: str, value: str):
        self.conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_settings(self) -> dict[str,str]:
        rows = self.conn.execute("SELECT key,value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # staff
    def list_staff(self, include_inactive=True):
        if include_inactive:
            rows = self.conn.execute("SELECT * FROM staff ORDER BY active DESC, id ASC").fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM staff WHERE active=1 ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]

    def upsert_staff(self, staff_id: int|None, name: str, phone: str, active: int):
        name, phone = name.strip(), phone.strip()
        cur = self.conn.cursor()
        if staff_id:
            cur.execute("UPDATE staff SET name=?, phone=?, active=? WHERE id=?", (name, phone, active, staff_id))
            sid = staff_id
        else:
            cur.execute("INSERT INTO staff(name,phone,active) VALUES(?,?,?)", (name, phone, active))
            sid = int(cur.lastrowid)
        self.conn.commit()
        return int(sid)

    def delete_staff(self, staff_id: int):
        self.conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
        self.conn.commit()

    def is_staff_phone(self, phone: str) -> tuple[bool, int|None]:
        r = self.conn.execute("SELECT id FROM staff WHERE phone=?", (phone.strip(),)).fetchone()
        return (True, int(r["id"])) if r else (False, None)

    # conversations / messages
    def upsert_conversation(self, phone: str) -> int:
        phone = phone.strip()
        is_staff, _ = self.is_staff_phone(phone)
        kind = "staff" if is_staff else "customer"
        self.conn.execute("INSERT OR IGNORE INTO conversations(phone,kind,last_time) VALUES(?,?,?)", (phone, kind, now_iso()))
        self.conn.execute("UPDATE conversations SET kind=?, last_time=? WHERE phone=?", (kind, now_iso(), phone))
        self.conn.commit()
        r = self.conn.execute("SELECT id FROM conversations WHERE phone=?", (phone,)).fetchone()
        return int(r["id"])

    def set_conversation_kind(self, phone: str, kind: str):
        self.conn.execute("UPDATE conversations SET kind=? WHERE phone=?", (kind, phone.strip()))
        self.conn.commit()

    def add_message(self, phone: str, direction: str, text: str, meta: dict[str,Any] | None=None) -> int:
        conv_id = self.upsert_conversation(phone)
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        t = now_iso()
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO messages(conv_id,direction,text,time,meta_json) VALUES(?,?,?,?,?)",
            (conv_id, direction, text, t, meta_json),
        )
        self.conn.execute("UPDATE conversations SET last_message=?, last_time=? WHERE id=?", (text[:120], t, conv_id))
        self.conn.commit()
        return int(cur.lastrowid)

    def list_conversations(self, q: str="", kind_filter: str="all") -> list[dict[str,Any]]:
        q = q.strip()
        where, params = [], []
        if kind_filter in ("customer","staff"):
            where.append("kind=?")
            params.append(kind_filter)
        if q:
            like = f"%{q}%"
            where.append("(phone LIKE ? OR display_name LIKE ? OR last_message LIKE ?)")
            params.extend([like, like, like])
        sql = "SELECT * FROM conversations"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY last_time DESC LIMIT 300"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_messages(self, conv_id: int, limit: int=400):
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE conv_id=? ORDER BY time ASC LIMIT ?",
            (conv_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, conv_id: int):
        r = self.conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
        return dict(r) if r else None

    # tasks
    def get_active_task_for_conv(self, conv_id: int):
        r = self.conn.execute(
            "SELECT * FROM tasks WHERE conv_id=? AND status IN ('TODO','HOLD','CONFIRMED','IN_PROGRESS') ORDER BY id DESC LIMIT 1",
            (conv_id,),
        ).fetchone()
        return dict(r) if r else None

    def create_or_update_task(self, conv_id: int, extracted: dict[str,Any]) -> int:
        row = self.conn.execute(
            "SELECT id FROM tasks WHERE conv_id=? AND status IN ('TODO','HOLD','CONFIRMED','IN_PROGRESS') ORDER BY id DESC LIMIT 1",
            (conv_id,),
        ).fetchone()
        now = now_iso()
        if row:
            tid = int(row["id"])
            self.conn.execute(
                "UPDATE tasks SET title=?, address=?, contact_phone=?, notes=?, updated_time=? WHERE id=?",
                (extracted.get("title",""), extracted.get("address",""), extracted.get("contact_phone",""), extracted.get("notes",""), now, tid),
            )
        else:
            cur = self.conn.cursor()
            cur.execute(
                "INSERT INTO tasks(conv_id,title,address,contact_phone,notes,status,created_time,updated_time) VALUES(?,?,?,?,?,'TODO',?,?)",
                (conv_id, extracted.get("title",""), extracted.get("address",""), extracted.get("contact_phone",""), extracted.get("notes",""), now, now),
            )
            tid = int(cur.lastrowid)
        self.conn.commit()
        return int(tid)

    def list_tasks(self, date_prefix: str="", staff_id: int|None=None, status: str="") -> list[dict[str,Any]]:
        where, params = [], []
        if date_prefix:
            where.append("start_time LIKE ?")
            params.append(f"{date_prefix}%")
        if staff_id:
            where.append("staff_id=?")
            params.append(staff_id)
        if status:
            where.append("status=?")
            params.append(status)
        sql = "SELECT * FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY COALESCE(start_time, created_time) ASC LIMIT 500"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def _overlap_conflict(self, staff_id: int, start_time: str, end_time: str) -> bool:
        # Check overlapping intervals with active tasks
        rows = self.conn.execute(
            "SELECT start_time, duration_min FROM tasks WHERE staff_id=? AND status IN ('HOLD','CONFIRMED','IN_PROGRESS') AND start_time IS NOT NULL",
            (staff_id,),
        ).fetchall()
        s_new = parse_iso(start_time)
        e_new = parse_iso(end_time)
        if not s_new or not e_new:
            return False
        from datetime import timedelta
        for r in rows:
            s = parse_iso(r["start_time"])
            if not s:
                continue
            e = s + timedelta(minutes=int(r["duration_min"] or 60))
            if max(s, s_new) < min(e, e_new):
                return True
        return False

    def assign_hold(self, task_id: int, staff_id: int, start_time: str, duration_min: int, hold_minutes: int):
        from datetime import datetime, timedelta
        now = now_iso()
        # compute end_time
        try:
            sdt = datetime.fromisoformat(start_time)
        except Exception:
            return False, "时间格式不对"
        end_time = (sdt + timedelta(minutes=duration_min)).isoformat(timespec="seconds")

        if self._overlap_conflict(staff_id, start_time, end_time):
            return False, "冲突：该员工该时间段已被占用"

        try:
            expires = (datetime.fromisoformat(now) + timedelta(minutes=hold_minutes)).isoformat(timespec="seconds")
        except Exception:
            expires = now
        try:
            self.conn.execute(
                "UPDATE tasks SET staff_id=?, start_time=?, duration_min=?, status='HOLD', hold_expires_at=?, updated_time=? WHERE id=?",
                (staff_id, start_time, duration_min, expires, now, task_id),
            )
            self.conn.commit()
            return True, "已临时占用（HOLD）"
        except sqlite3.IntegrityError:
            return False, "冲突：该员工该开始时间已被占用"

    def confirm_task(self, task_id: int):
        self.conn.execute("UPDATE tasks SET status='CONFIRMED', hold_expires_at=NULL, updated_time=? WHERE id=?", (now_iso(), task_id))
        self.conn.commit()

    def mark_done(self, task_id: int):
        self.conn.execute("UPDATE tasks SET status='DONE', updated_time=? WHERE id=?", (now_iso(), task_id))
        self.conn.commit()

    def cancel_task(self, task_id: int):
        self.conn.execute("UPDATE tasks SET status='CANCELLED', updated_time=? WHERE id=?", (now_iso(), task_id))
        self.conn.commit()

    def cleanup_expired_holds(self) -> int:
        rows = self.conn.execute("SELECT id, hold_expires_at FROM tasks WHERE status='HOLD' AND hold_expires_at IS NOT NULL").fetchall()
        now = parse_iso(now_iso())
        expired = []
        for r in rows:
            dt = parse_iso(r["hold_expires_at"])
            if dt and now and dt <= now:
                expired.append(int(r["id"]))
        if not expired:
            return 0
        self.conn.executemany(
            "UPDATE tasks SET status='EXPIRED', staff_id=NULL, start_time=NULL, hold_expires_at=NULL, updated_time=? WHERE id=?",
            [(now_iso(), tid) for tid in expired],
        )
        self.conn.commit()
        return len(expired)

    # staff requests (leave)
    def create_staff_request(self, staff_id: int, content: str, start_time: str|None, end_time: str|None) -> int:
        now = now_iso()
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO staff_requests(staff_id,type,content,start_time,end_time,status,created_time,updated_time) VALUES(?,?,?,?,?,'PENDING',?,?)",
            (staff_id, "leave", content, start_time, end_time, now, now),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_staff_requests(self, status: str="") -> list[dict[str,Any]]:
        if status:
            rows = self.conn.execute("SELECT * FROM staff_requests WHERE status=? ORDER BY created_time DESC LIMIT 500", (status,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM staff_requests ORDER BY created_time DESC LIMIT 500").fetchall()
        return [dict(r) for r in rows]

    def update_staff_request_status(self, req_id: int, status: str):
        self.conn.execute("UPDATE staff_requests SET status=?, updated_time=? WHERE id=?", (status, now_iso(), req_id))
        self.conn.commit()

    # KB
    def list_kb(self, q: str="") -> list[dict[str,Any]]:
        q = q.strip()
        where, params = [], []
        if q:
            like = f"%{q}%"
            where.append("(title LIKE ? OR content LIKE ? OR tags LIKE ?)")
            params.extend([like, like, like])
        sql = "SELECT * FROM kb_entries"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_time DESC LIMIT 500"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def upsert_kb(self, kb_id: int|None, title: str, content: str, tags: str, enabled: int) -> int:
        title, content, tags = title.strip(), content.strip(), tags.strip()
        now = now_iso()
        cur = self.conn.cursor()
        if kb_id:
            cur.execute(
                "UPDATE kb_entries SET title=?, content=?, tags=?, enabled=?, version=version+1, updated_time=? WHERE id=?",
                (title, content, tags, enabled, now, kb_id),
            )
            kid = kb_id
        else:
            cur.execute(
                "INSERT INTO kb_entries(title,content,tags,enabled,version,updated_time) VALUES(?,?,?,?,?,?)",
                (title, content, tags, enabled, 1, now),
            )
            kid = int(cur.lastrowid)
        self.conn.commit()
        return int(kid)

    def delete_kb(self, kb_id: int):
        self.conn.execute("DELETE FROM kb_entries WHERE id=?", (kb_id,))
        self.conn.commit()
