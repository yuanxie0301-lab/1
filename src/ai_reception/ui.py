from __future__ import annotations
import json
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

from .db import DB
from .i18n import t as tr
from .sms_gateway import SmsGateway
from .extract import extract_customer_task_fields, detect_leave_request
from .llm_router import LLMRouter, LLMConfig
from .kb_search import pick_kb_context
from .timeutil import parse_friendly_dt, dt_to_iso

def _safe_int(s: str, default: int) -> int:
    try: return int(str(s).strip())
    except Exception: return default

class RootUI(ttk.Frame):
    """
    WeChat desktop-ish layout:
      - left sidebar icons: chat/schedule/staff/me
      - main area: current view
    """
    def __init__(self, master: tk.Misc, db: DB):
        super().__init__(master)
        self.db = db
        self.style = ttk.Style()
        try: self.style.theme_use("clam")
        except Exception: pass

        self.lang = self.db.get_setting("lang") or "zh"

        # outer: sidebar + main
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True)

        self.sidebar = ttk.Frame(outer, width=56)
        self.sidebar.pack(side="left", fill="y")

        self.main = ttk.Frame(outer)
        self.main.pack(side="left", fill="both", expand=True)

        self.views = {}
        self._build_views()
        self._build_sidebar()

        self.show("chat")
        self.after(2500, self._tick)

    def _tick(self):
        try:
            n = self.db.cleanup_expired_holds()
            v = self.views.get("chat")
            if n and v and hasattr(v, "set_status"):
                v.set_status(f"{n} HOLD expired")
            for v in self.views.values():
                if hasattr(v, "on_tick"):
                    v.on_tick()
        finally:
            self.after(2500, self._tick)

    def _build_views(self):
        for c in list(self.main.winfo_children()):
            c.destroy()
        self.views = {
            "chat": ChatView(self.main, self.db, get_lang=lambda: self.lang),
            "schedule": ScheduleView(self.main, self.db, get_lang=lambda: self.lang),
            "staff": StaffView(self.main, self.db, get_lang=lambda: self.lang),
            "me": MeView(self.main, self.db, get_lang=lambda: self.lang, on_lang_changed=self.rebuild),
        }
        for v in self.views.values():
            v.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _build_sidebar(self):
        for c in list(self.sidebar.winfo_children()):
            c.destroy()

        # simple emoji buttons (no assets)
        btns = [
            ("💬", "chat", tr(self.lang, "tab_reception")),
            ("📅", "schedule", tr(self.lang, "tab_schedule")),
            ("🧑‍💼", "staff", tr(self.lang, "tab_staff")),
            ("⚙️", "me", tr(self.lang, "tab_me")),
        ]
        ttk.Label(self.sidebar, text="").pack(pady=6)
        for icon, key, tip in btns:
            b = ttk.Button(self.sidebar, text=icon, width=3, command=lambda k=key: self.show(k))
            b.pack(pady=6, padx=6)
            # tooltip-lite: show label below
            ttk.Label(self.sidebar, text=tip, wraplength=48, justify="center").pack(pady=(0,8))

    def show(self, key: str):
        v = self.views.get(key)
        if v:
            v.lift()
            if hasattr(v, "on_show"):
                v.on_show()

    def rebuild(self):
        self.lang = self.db.get_setting("lang") or "zh"
        self._build_views()
        self._build_sidebar()
        self.show("chat")

class ChatView(ttk.Frame):
    """
    Three columns:
      - convo list
      - chat
      - right task panel
    """
    def __init__(self, master: tk.Misc, db: DB, get_lang):
        super().__init__(master)
        self.db = db
        self.get_lang = get_lang
        self.current_conv_id: int | None = None
        self.current_phone: str = ""
        self.current_task_id: int | None = None

        self._build()
        self.refresh()

    def lang(self): return self.get_lang()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=6)

        ttk.Label(top, text=tr(self.lang(),"search")).pack(side="left")
        self.q = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.q, width=26)
        e.pack(side="left", padx=6)
        e.bind("<KeyRelease>", lambda ev: self.refresh_convs())

        # kind filter like tabs
        self.kind = tk.StringVar(value="all")
        for k, label in [("all", tr(self.lang(),"all")), ("customer", tr(self.lang(),"customers")), ("staff", tr(self.lang(),"employees"))]:
            ttk.Radiobutton(top, text=label, value=k, variable=self.kind, command=self.refresh_convs).pack(side="left", padx=6)

        ttk.Button(top, text=tr(self.lang(),"simulate_in"), command=self.sim_inbound).pack(side="right", padx=6)
        ttk.Button(top, text=tr(self.lang(),"make_task"), command=self.make_task_from_chat).pack(side="right", padx=6)
        ttk.Button(top, text=tr(self.lang(),"ai_reply_once"), command=self.ai_reply_once).pack(side="right", padx=6)

        self.status = ttk.Label(self, text="")
        self.status.pack(anchor="w", padx=10)

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=8)

        # left: conv list
        left = ttk.Frame(paned)
        paned.add(left, weight=1)
        self.conv_list = tk.Listbox(left, activestyle="none")
        self.conv_list.pack(side="left", fill="both", expand=True)
        self.conv_list.bind("<<ListboxSelect>>", lambda e: self.on_select_conv())
        sb = ttk.Scrollbar(left, orient="vertical", command=self.conv_list.yview)
        sb.pack(side="right", fill="y")
        self.conv_list.config(yscrollcommand=sb.set)

        # mid: chat
        mid = ttk.Frame(paned)
        paned.add(mid, weight=3)
        self.chat = tk.Text(mid, wrap="word", state="disabled")
        self.chat.pack(fill="both", expand=True)

        bottom = ttk.Frame(mid)
        bottom.pack(fill="x", pady=6)
        self.input = tk.StringVar()
        inp = ttk.Entry(bottom, textvariable=self.input)
        inp.pack(side="left", fill="x", expand=True, padx=8)
        inp.bind("<Return>", lambda e: self.send())
        ttk.Button(bottom, text=tr(self.lang(),"send"), command=self.send).pack(side="right", padx=6)

        # right: task panel (like WeChat contact info panel)
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        ttk.Label(right, text=tr(self.lang(),"dispatch_panel")).pack(anchor="w")

        self.contact_info = tk.Text(right, height=5, wrap="word", state="disabled")
        self.contact_info.pack(fill="x", pady=(6,4))

        self.task_info = tk.Text(right, height=9, wrap="word", state="disabled")
        self.task_info.pack(fill="x", pady=4)

        form = ttk.Frame(right)
        form.pack(fill="x", pady=4)
        ttk.Label(form, text=tr(self.lang(),"staff")).grid(row=0, column=0, sticky="w")
        self.staff_var = tk.StringVar()
        self.staff_cb = ttk.Combobox(form, textvariable=self.staff_var, state="readonly")
        self.staff_cb.grid(row=0, column=1, sticky="we", padx=6)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text=tr(self.lang(),"start_time")).grid(row=1, column=0, sticky="w", pady=6)
        self.start_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.start_var).grid(row=1, column=1, sticky="we", padx=6)
        ttk.Label(form, text=tr(self.lang(),"hint_time")).grid(row=2, column=1, sticky="w", padx=6)

        dur = ttk.Frame(right)
        dur.pack(fill="x", pady=4)
        ttk.Label(dur, text="Duration(min)").pack(side="left")
        self.dur_var = tk.StringVar(value="60")
        ttk.Entry(dur, textvariable=self.dur_var, width=6).pack(side="left", padx=6)

        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=6)
        ttk.Button(btns, text=tr(self.lang(),"hold"), command=self.hold).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(btns, text=tr(self.lang(),"confirm"), command=self.confirm).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(btns, text=tr(self.lang(),"done"), command=self.done).pack(side="left", expand=True, fill="x", padx=2)

        btns2 = ttk.Frame(right)
        btns2.pack(fill="x")
        ttk.Button(btns2, text=tr(self.lang(),"cancel"), command=self.cancel).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(btns2, text=tr(self.lang(),"refresh"), command=self.refresh_task_panel).pack(side="left", expand=True, fill="x", padx=2)

    def on_show(self):
        self.refresh()

    def on_tick(self):
        if self.current_conv_id:
            self.refresh_task_panel()

    def set_status(self, s: str):
        self.status.config(text=s)

    def refresh(self):
        self.refresh_staff()
        self.refresh_convs()
        if self.current_conv_id:
            self.load_msgs(self.current_conv_id)
            self.refresh_task_panel()

    def refresh_staff(self):
        staff = self.db.list_staff(include_inactive=False)
        self.staff_rows = staff
        vals = [f"{s['id']}: {s['name']} ({s['phone']})" for s in staff]
        self.staff_cb["values"] = vals
        if vals and not self.staff_var.get():
            self.staff_var.set(vals[0])

    def refresh_convs(self):
        k = self.kind.get().strip()
        rows = self.db.list_conversations(self.q.get(), kind_filter=k)
        self.conv_rows = rows
        self.conv_list.delete(0, tk.END)
        for r in rows:
            phone = r.get("phone","")
            tag = "👤" if r.get("kind") == "customer" else "🧑‍💼"
            last = (r.get("last_message") or "")[:24]
            self.conv_list.insert(tk.END, f"{tag} {phone} | {last}")

    def on_select_conv(self):
        sel = self.conv_list.curselection()
        if not sel:
            return
        r = self.conv_rows[int(sel[0])]
        self.current_conv_id = int(r["id"])
        self.current_phone = r["phone"]
        self.load_msgs(self.current_conv_id)
        self.refresh_contact_panel()
        self.refresh_task_panel()

    def refresh_contact_panel(self):
        if not self.current_conv_id:
            return
        conv = self.db.get_conversation(self.current_conv_id)
        if not conv:
            return
        self.contact_info.config(state="normal")
        self.contact_info.delete("1.0", tk.END)
        kind = conv.get("kind")
        tag = "客户" if kind == "customer" else "员工"
        self.contact_info.insert(tk.END, f"{tag}\n电话：{conv.get('phone','')}")
        self.contact_info.config(state="disabled")

    def load_msgs(self, conv_id: int):
        msgs = self.db.get_messages(conv_id, limit=400)
        self.chat.config(state="normal")
        self.chat.delete("1.0", tk.END)
        lang = self.lang()
        for m in msgs:
            d = m["direction"]
            prefix = "对方" if d == "in" else ("我方" if d == "out" else "系统")
            meta = {}
            try: meta = json.loads(m.get("meta_json") or "{}")
            except Exception: meta = {}
            suffix = ""
            if d == "out" and meta.get("channel") == "sms":
                st = meta.get("status") or "sent"
                suffix = "  " + (tr(lang, "msg_sent_sim") if st == "sent" else tr(lang, "msg_failed_sim"))
            self.chat.insert(tk.END, f"{prefix}：{m['text']}{suffix}\n")
        self.chat.config(state="disabled")
        self.chat.see(tk.END)

    def _sms_gateway(self) -> SmsGateway:
        return SmsGateway(self.db.get_setting("sms_mode"))

    def send(self):
        if not self.current_phone:
            messagebox.showinfo("提示", "先选一个会话（或模拟收到短信创建会话）")
            return
        text = self.input.get().strip()
        if not text:
            return
        ok, status, msg_id = self._sms_gateway().send_sms(self.current_phone, text)
        self.db.add_message(self.current_phone, "out", text, meta={"channel":"sms","status":status,"msg_id":msg_id})
        self.input.set("")
        self.load_msgs(self.current_conv_id)
        self.refresh_convs()

    def sim_inbound(self):
        dlg = tk.Toplevel(self)
        dlg.title(tr(self.lang(),"simulate_in"))
        dlg.geometry("520x260")
        dlg.transient(self)
        dlg.grab_set()

        phone_var = tk.StringVar(value=self.current_phone or "0210000000")
        msg_var = tk.StringVar(value="我想预约明天 14:30，到XXX地址，电话0211234567。")

        ttk.Label(dlg, text=tr(self.lang(),"phone")).pack(anchor="w", padx=12, pady=(12,2))
        ttk.Entry(dlg, textvariable=phone_var).pack(fill="x", padx=12)

        ttk.Label(dlg, text="Message").pack(anchor="w", padx=12, pady=(10,2))
        ttk.Entry(dlg, textvariable=msg_var).pack(fill="x", padx=12)

        def do_it():
            phone = phone_var.get().strip()
            msg = msg_var.get().strip()
            if not phone or not msg:
                return
            self.db.add_message(phone, "in", msg, meta={"channel":"sms","status":"received"})
            self._maybe_handle_staff_incoming(phone, msg)
            self.refresh_convs()
            # auto select
            self.conv_list.selection_clear(0, tk.END)
            for i, r in enumerate(self.conv_rows):
                if r["phone"] == phone:
                    self.conv_list.selection_set(i)
                    break
            self.on_select_conv()
            dlg.destroy()

        ttk.Button(dlg, text="OK", command=do_it).pack(pady=12)
        ttk.Label(dlg, text="（测试用：不会发真实短信）").pack()

    def _maybe_handle_staff_incoming(self, phone: str, msg: str):
        is_staff, staff_id = self.db.is_staff_phone(phone)
        if not is_staff or not staff_id:
            return
        leave = detect_leave_request(msg)
        if leave:
            req_id = self.db.create_staff_request(staff_id, leave["content"], leave.get("start_time"), leave.get("end_time"))
            self.db.add_message(phone, "sys", f"已收到请假申请（ID {req_id}），等待管理员处理。", meta={"channel":"sys"})

    def _router(self) -> LLMRouter:
        s = self.db.get_settings()
        return LLMRouter(LLMConfig(
            mode=s.get("llm_mode","local_first"),
            ollama_base_url=s.get("ollama_base_url","http://localhost:11434"),
            ollama_model=s.get("ollama_model","llama3.1:8b"),
            cloud_base_url=s.get("cloud_base_url","https://api.openai.com"),
            cloud_api_key=s.get("cloud_api_key",""),
            cloud_model=s.get("cloud_model","gpt-4o-mini"),
        ))

    def ai_reply_once(self):
        if not self.current_conv_id:
            return
        conv = self.db.get_conversation(self.current_conv_id)
        if not conv:
            return
        if conv.get("kind") == "staff":
            self._send_text("收到，我这边看一下安排/请假。")
            return

        msgs = self.db.get_messages(self.current_conv_id, limit=120)
        history = []
        for m in msgs[-12:]:
            role = "user" if m["direction"] == "in" else "assistant"
            if m["direction"] == "sys":
                role = "system"
            history.append({"role": role, "content": m["text"]})

        last_user = ""
        for m in reversed(msgs):
            if m["direction"] == "in":
                last_user = m["text"]
                break

        kb_ctx = pick_kb_context(last_user, self.db.list_kb(q=last_user))
        system = {"role":"system","content":"你是短信接待与派单助手。回复尽量短：确认时间、地址、联系电话、需求。缺什么就问一句。"}
        ok, out = self._router().chat([system] + kb_ctx + history)
        if not ok or not out:
            out = "收到～麻烦补充：时间、地址、联系电话、以及具体要求。"
        self._send_text(out)

    def _send_text(self, text: str):
        if not self.current_phone:
            return
        ok, status, msg_id = self._sms_gateway().send_sms(self.current_phone, text)
        self.db.add_message(self.current_phone, "out", text, meta={"channel":"sms","status":status,"msg_id":msg_id})
        self.load_msgs(self.current_conv_id)
        self.refresh_convs()

    def make_task_from_chat(self):
        if not self.current_conv_id:
            messagebox.showinfo("提示", "先选择一个客户会话")
            return
        conv = self.db.get_conversation(self.current_conv_id)
        if conv and conv.get("kind") == "staff":
            messagebox.showinfo("提示", "员工会话不生成客户任务")
            return
        msgs = self.db.get_messages(self.current_conv_id, limit=400)
        last_in = None
        for m in reversed(msgs):
            if m["direction"] == "in":
                last_in = m
                break
        if not last_in:
            messagebox.showinfo("提示", "没有对方消息")
            return
        extracted = extract_customer_task_fields(last_in["text"], fallback_phone=self.current_phone)
        self.current_task_id = self.db.create_or_update_task(self.current_conv_id, extracted)
        self.refresh_task_panel()
        self.set_status("任务已更新")

    def refresh_task_panel(self):
        if not self.current_conv_id:
            return
        task = self.db.get_active_task_for_conv(self.current_conv_id)
        self.current_task_id = task["id"] if task else None

        self.task_info.config(state="normal")
        self.task_info.delete("1.0", tk.END)
        if not task:
            self.task_info.insert(tk.END, "暂无任务：点“从聊天生成任务”。")
        else:
            staff_name = ""
            if task.get("staff_id"):
                for s in self.db.list_staff(include_inactive=True):
                    if int(s["id"]) == int(task["staff_id"]):
                        staff_name = s["name"]
                        break
            lines = [
                f"{tr(self.lang(),'status')}：{task.get('status','')}",
                f"{tr(self.lang(),'title')}：{task.get('title','')}",
                f"{tr(self.lang(),'phone')}：{task.get('contact_phone','')}",
                f"{tr(self.lang(),'address')}：{task.get('address','')}",
                f"{tr(self.lang(),'start_time')}：{(task.get('start_time') or '').replace('T',' ')}",
                f"{tr(self.lang(),'staff')}：{staff_name}",
                f"{tr(self.lang(),'notes')}：{(task.get('notes','') or '')[:140]}",
            ]
            self.task_info.insert(tk.END, "\n".join(lines))
            if task.get("start_time"):
                self.start_var.set(task["start_time"].replace("T"," "))
        self.task_info.config(state="disabled")

    def _parse_staff_id(self):
        v = self.staff_var.get().strip()
        if not v:
            return None
        try:
            return int(v.split(":")[0])
        except Exception:
            return None

    def hold(self):
        if not self.current_task_id:
            messagebox.showinfo("提示", "先生成任务")
            return
        staff_id = self._parse_staff_id()
        dt = parse_friendly_dt(self.start_var.get())
        if not staff_id or not dt:
            messagebox.showinfo("提示", tr(self.lang(),"hint_time"))
            return
        start_iso = dt_to_iso(dt)
        dur = _safe_int(self.dur_var.get(), 60)
        hold_minutes = _safe_int(self.db.get_setting("hold_minutes"), 10)
        ok, msg = self.db.assign_hold(self.current_task_id, staff_id, start_iso, dur, hold_minutes)
        if not ok:
            messagebox.showwarning("冲突", msg)
        self.refresh_task_panel()
        self.set_status(msg)

    def confirm(self):
        if not self.current_task_id:
            return
        self.db.confirm_task(self.current_task_id)
        self.refresh_task_panel()
        self.set_status("CONFIRMED")

    def done(self):
        if not self.current_task_id:
            return
        self.db.mark_done(self.current_task_id)
        self.refresh_task_panel()
        self.set_status("DONE")

    def cancel(self):
        if not self.current_task_id:
            return
        if messagebox.askyesno("确认", "确定取消？"):
            self.db.cancel_task(self.current_task_id)
            self.refresh_task_panel()
            self.set_status("CANCELLED")

class ScheduleView(ttk.Frame):
    def __init__(self, master: tk.Misc, db: DB, get_lang):
        super().__init__(master)
        self.db = db
        self.get_lang = get_lang
        self._build()
        self.refresh()

    def lang(self): return self.get_lang()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Label(top, text="Date (YYYY-MM-DD)").pack(side="left")
        self.date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        ttk.Entry(top, textvariable=self.date_var, width=12).pack(side="left", padx=6)

        ttk.Label(top, text=tr(self.lang(),"staff")).pack(side="left", padx=(12,2))
        self.staff_var = tk.StringVar(value="all")
        self.staff_cb = ttk.Combobox(top, textvariable=self.staff_var, state="readonly")
        self.staff_cb.pack(side="left", padx=6)

        ttk.Button(top, text=tr(self.lang(),"refresh"), command=self.refresh).pack(side="right")

        self.tree = ttk.Treeview(self, columns=("time","staff","status","title"), show="headings")
        for c, txt, w in [("time","Time",170),("staff","Staff",140),("status","Status",110),("title","Title",520)]:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True, padx=8, pady=8)

    def on_show(self):
        self.refresh()

    def refresh(self):
        staff = self.db.list_staff(include_inactive=False)
        self.staff_rows = staff
        vals = ["all"] + [f"{s['id']}: {s['name']}" for s in staff]
        self.staff_cb["values"] = vals
        if self.staff_var.get() not in vals:
            self.staff_var.set("all")

        date_prefix = self.date_var.get().strip()
        sid = None
        if self.staff_var.get() != "all":
            try: sid = int(self.staff_var.get().split(":")[0])
            except Exception: sid = None

        rows = self.db.list_tasks(date_prefix=date_prefix, staff_id=sid, status="")
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            staff_name = ""
            if r.get("staff_id"):
                for s in staff:
                    if int(s["id"]) == int(r["staff_id"]):
                        staff_name = s["name"]
                        break
            self.tree.insert("", "end", values=((r.get("start_time") or "").replace("T"," "),
                                                staff_name, r.get("status",""), r.get("title","")))

class StaffView(ttk.Frame):
    def __init__(self, master: tk.Misc, db: DB, get_lang):
        super().__init__(master)
        self.db = db
        self.get_lang = get_lang
        self.staff_id = None
        self.req_id = None
        self._build()
        self.refresh()

    def lang(self): return self.get_lang()

    def _build(self):
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(paned)
        paned.add(left, weight=2)

        top = ttk.Frame(left)
        top.pack(fill="x", pady=6)
        ttk.Button(top, text=tr(self.lang(),"new"), command=self.new_staff).pack(side="left")
        ttk.Button(top, text=tr(self.lang(),"save"), command=self.save_staff).pack(side="left", padx=6)
        ttk.Button(top, text=tr(self.lang(),"delete"), command=self.delete_staff).pack(side="left", padx=6)
        ttk.Button(top, text=tr(self.lang(),"refresh"), command=self.refresh).pack(side="right")

        body = ttk.Frame(left)
        body.pack(fill="both", expand=True)

        self.staff_list = tk.Listbox(body, activestyle="none")
        self.staff_list.pack(side="left", fill="both", expand=True)
        self.staff_list.bind("<<ListboxSelect>>", lambda e: self.load_staff())

        sb = ttk.Scrollbar(body, orient="vertical", command=self.staff_list.yview)
        sb.pack(side="left", fill="y")
        self.staff_list.config(yscrollcommand=sb.set)

        editor = ttk.Frame(body)
        editor.pack(side="left", fill="both", expand=True, padx=(10,0))

        self.name_var = tk.StringVar()
        self.phone_var = tk.StringVar()
        self.active_var = tk.IntVar(value=1)

        ttk.Label(editor, text=tr(self.lang(),"name")).pack(anchor="w")
        ttk.Entry(editor, textvariable=self.name_var).pack(fill="x", pady=(0,8))

        ttk.Label(editor, text=tr(self.lang(),"phone")).pack(anchor="w")
        ttk.Entry(editor, textvariable=self.phone_var).pack(fill="x", pady=(0,8))

        ttk.Checkbutton(editor, text=tr(self.lang(),"active"), variable=self.active_var).pack(anchor="w", pady=(0,8))

        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        ttk.Label(right, text=tr(self.lang(),"leave_requests")).pack(anchor="w")

        self.req_tree = ttk.Treeview(right, columns=("id","staff","status","time","content"), show="headings")
        for c, txt, w in [("id","ID",60),("staff","Staff",120),("status","Status",110),("time","Time",160),("content","Content",360)]:
            self.req_tree.heading(c, text=txt)
            self.req_tree.column(c, width=w)
        self.req_tree.pack(fill="both", expand=True, pady=8)
        self.req_tree.bind("<<TreeviewSelect>>", lambda e: self.on_select_req())

        ops = ttk.Frame(right)
        ops.pack(fill="x")
        ttk.Button(ops, text=tr(self.lang(),"approve"), command=lambda: self.set_req_status("APPROVED")).pack(side="left")
        ttk.Button(ops, text=tr(self.lang(),"reject"), command=lambda: self.set_req_status("REJECTED")).pack(side="left", padx=6)

    def on_show(self):
        self.refresh()

    def refresh(self):
        rows = self.db.list_staff(include_inactive=True)
        self.staff_rows = rows
        self.staff_list.delete(0, tk.END)
        for r in rows:
            tag = "" if r.get("active") else " (off)"
            self.staff_list.insert(tk.END, f"{r['id']}: {r['name']} {r['phone']}{tag}")

        self.refresh_requests()

    def new_staff(self):
        self.staff_id = None
        self.name_var.set("")
        self.phone_var.set("")
        self.active_var.set(1)

    def load_staff(self):
        sel = self.staff_list.curselection()
        if not sel:
            return
        r = self.staff_rows[int(sel[0])]
        self.staff_id = int(r["id"])
        self.name_var.set(r.get("name",""))
        self.phone_var.set(r.get("phone",""))
        self.active_var.set(1 if r.get("active") else 0)

    def save_staff(self):
        name = self.name_var.get().strip()
        phone = self.phone_var.get().strip()
        if not name or not phone:
            messagebox.showwarning("提示", "姓名和手机号不能为空")
            return
        try:
            self.staff_id = self.db.upsert_staff(self.staff_id, name, phone, int(self.active_var.get()))
            self.refresh()
        except Exception as e:
            messagebox.showerror("错误", f"保存失败：{e}")

    def delete_staff(self):
        if not self.staff_id:
            return
        if messagebox.askyesno("确认", "确定删除该员工？"):
            self.db.delete_staff(self.staff_id)
            self.new_staff()
            self.refresh()

    def refresh_requests(self):
        reqs = self.db.list_staff_requests(status="")
        staff_map = {s["id"]: s["name"] for s in self.staff_rows}
        self.req_rows = reqs
        self.req_tree.delete(*self.req_tree.get_children())
        for r in reqs:
            staff_name = staff_map.get(r["staff_id"], str(r["staff_id"]))
            self.req_tree.insert("", "end", values=(r["id"], staff_name, r["status"], (r["created_time"] or "").replace("T"," "), (r["content"] or "")[:80]))

    def on_select_req(self):
        sel = self.req_tree.selection()
        if not sel:
            self.req_id = None
            return
        vals = self.req_tree.item(sel[0]).get("values") or []
        self.req_id = int(vals[0]) if vals else None

    def set_req_status(self, status: str):
        if not self.req_id:
            return
        self.db.update_staff_request_status(self.req_id, status)
        self.refresh_requests()

class MeView(ttk.Frame):
    def __init__(self, master: tk.Misc, db: DB, get_lang, on_lang_changed):
        super().__init__(master)
        self.db = db
        self.get_lang = get_lang
        self.on_lang_changed = on_lang_changed
        self.kb_id = None
        self._build()
        self.load_settings()
        self.refresh_kb()

    def lang(self): return self.get_lang()

    def _build(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self.tab_settings = ttk.Frame(nb)
        self.tab_kb = ttk.Frame(nb)
        nb.add(self.tab_settings, text=tr(self.lang(),"settings"))
        nb.add(self.tab_kb, text=tr(self.lang(),"kb"))

        # settings
        f = self.tab_settings
        pad = {"padx":10, "pady":8}
        row = 0

        ttk.Label(f, text=tr(self.lang(),"language")).grid(row=row, column=0, sticky="w", **pad)
        self.lang_var = tk.StringVar()
        ttk.Combobox(f, textvariable=self.lang_var, state="readonly", values=["zh","en"]).grid(row=row, column=1, sticky="we", **pad)
        row += 1

        ttk.Label(f, text=tr(self.lang(),"sms_mode")).grid(row=row, column=0, sticky="w", **pad)
        self.sms_var = tk.StringVar()
        ttk.Combobox(f, textvariable=self.sms_var, state="readonly", values=["simulator","off"]).grid(row=row, column=1, sticky="we", **pad)
        row += 1

        ttk.Label(f, text=tr(self.lang(),"llm_mode")).grid(row=row, column=0, sticky="w", **pad)
        self.llm_var = tk.StringVar()
        ttk.Combobox(f, textvariable=self.llm_var, state="readonly", values=["local_first","cloud_first","off"]).grid(row=row, column=1, sticky="we", **pad)
        row += 1

        ttk.Label(f, text="Ollama URL").grid(row=row, column=0, sticky="w", **pad)
        self.ollama_url = tk.StringVar()
        ttk.Entry(f, textvariable=self.ollama_url).grid(row=row, column=1, sticky="we", **pad)
        row += 1

        ttk.Label(f, text="Ollama Model").grid(row=row, column=0, sticky="w", **pad)
        self.ollama_model = tk.StringVar()
        ttk.Entry(f, textvariable=self.ollama_model).grid(row=row, column=1, sticky="we", **pad)
        row += 1

        ttk.Label(f, text="Cloud API Key").grid(row=row, column=0, sticky="w", **pad)
        self.cloud_key = tk.StringVar()
        ttk.Entry(f, textvariable=self.cloud_key, show="*").grid(row=row, column=1, sticky="we", **pad)
        row += 1

        ttk.Label(f, text="Cloud Model").grid(row=row, column=0, sticky="w", **pad)
        self.cloud_model = tk.StringVar()
        ttk.Entry(f, textvariable=self.cloud_model).grid(row=row, column=1, sticky="we", **pad)
        row += 1

        ttk.Label(f, text="HOLD minutes").grid(row=row, column=0, sticky="w", **pad)
        self.hold_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.hold_var).grid(row=row, column=1, sticky="we", **pad)
        row += 1

        btns = ttk.Frame(f)
        btns.grid(row=row, column=0, columnspan=2, sticky="we", padx=10, pady=16)
        ttk.Button(btns, text=tr(self.lang(),"save"), command=self.save_settings).pack(side="left")
        ttk.Label(btns, text="Data folder: user_data/").pack(side="left", padx=12)

        f.columnconfigure(1, weight=1)

        # KB tab
        k = self.tab_kb
        top = ttk.Frame(k)
        top.pack(fill="x", padx=8, pady=8)

        ttk.Label(top, text=tr(self.lang(),"search")).pack(side="left")
        self.kb_q = tk.StringVar()
        ee = ttk.Entry(top, textvariable=self.kb_q, width=28)
        ee.pack(side="left", padx=6)
        ee.bind("<KeyRelease>", lambda ev: self.refresh_kb())

        ttk.Button(top, text=tr(self.lang(),"new"), command=self.kb_new).pack(side="left", padx=6)
        ttk.Button(top, text=tr(self.lang(),"save"), command=self.kb_save).pack(side="left", padx=6)
        ttk.Button(top, text=tr(self.lang(),"delete"), command=self.kb_delete).pack(side="left", padx=6)

        body = ttk.Frame(k)
        body.pack(fill="both", expand=True, padx=8, pady=8)

        self.kb_list = tk.Listbox(body, activestyle="none")
        self.kb_list.pack(side="left", fill="both", expand=True)
        self.kb_list.bind("<<ListboxSelect>>", lambda e: self.kb_load())

        sb = ttk.Scrollbar(body, orient="vertical", command=self.kb_list.yview)
        sb.pack(side="left", fill="y")
        self.kb_list.config(yscrollcommand=sb.set)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(10,0))

        self.kb_title = tk.StringVar()
        self.kb_tags = tk.StringVar()
        self.kb_enabled = tk.IntVar(value=1)

        ttk.Label(right, text=tr(self.lang(),"title")).pack(anchor="w")
        ttk.Entry(right, textvariable=self.kb_title).pack(fill="x", pady=(0,8))

        ttk.Label(right, text="Tags").pack(anchor="w")
        ttk.Entry(right, textvariable=self.kb_tags).pack(fill="x", pady=(0,8))

        ttk.Checkbutton(right, text=tr(self.lang(),"enabled"), variable=self.kb_enabled).pack(anchor="w", pady=(0,8))

        ttk.Label(right, text="Content").pack(anchor="w")
        self.kb_content = tk.Text(right, height=16, wrap="word")
        self.kb_content.pack(fill="both", expand=True)

    def on_show(self):
        self.load_settings()
        self.refresh_kb()

    def load_settings(self):
        s = self.db.get_settings()
        self.lang_var.set(s.get("lang","zh"))
        self.sms_var.set(s.get("sms_mode","simulator"))
        self.llm_var.set(s.get("llm_mode","local_first"))
        self.ollama_url.set(s.get("ollama_base_url","http://localhost:11434"))
        self.ollama_model.set(s.get("ollama_model","llama3.1:8b"))
        self.cloud_key.set(s.get("cloud_api_key",""))
        self.cloud_model.set(s.get("cloud_model","gpt-4o-mini"))
        self.hold_var.set(s.get("hold_minutes","10"))

    def save_settings(self):
        self.db.set_setting("lang", self.lang_var.get().strip() or "zh")
        self.db.set_setting("sms_mode", self.sms_var.get().strip() or "simulator")
        self.db.set_setting("llm_mode", self.llm_var.get().strip() or "local_first")
        self.db.set_setting("ollama_base_url", self.ollama_url.get().strip())
        self.db.set_setting("ollama_model", self.ollama_model.get().strip())
        self.db.set_setting("cloud_api_key", self.cloud_key.get().strip())
        self.db.set_setting("cloud_model", self.cloud_model.get().strip())
        self.db.set_setting("hold_minutes", self.hold_var.get().strip() or "10")
        messagebox.showinfo("OK", "Saved")
        self.on_lang_changed()

    # KB
    def refresh_kb(self):
        rows = self.db.list_kb(self.kb_q.get())
        self.kb_rows = rows
        self.kb_list.delete(0, tk.END)
        for r in rows:
            flag = "" if r.get("enabled") else " (off)"
            self.kb_list.insert(tk.END, f"{r.get('title','')}{flag}")

    def kb_new(self):
        self.kb_id = None
        self.kb_title.set("")
        self.kb_tags.set("")
        self.kb_enabled.set(1)
        self.kb_content.delete("1.0", tk.END)

    def kb_load(self):
        sel = self.kb_list.curselection()
        if not sel: return
        r = self.kb_rows[int(sel[0])]
        self.kb_id = int(r["id"])
        self.kb_title.set(r.get("title",""))
        self.kb_tags.set(r.get("tags",""))
        self.kb_enabled.set(1 if r.get("enabled") else 0)
        self.kb_content.delete("1.0", tk.END)
        self.kb_content.insert(tk.END, r.get("content",""))

    def kb_save(self):
        title = self.kb_title.get().strip()
        content = self.kb_content.get("1.0", tk.END).strip()
        if not title or not content:
            messagebox.showwarning("提示", "标题和内容不能为空")
            return
        self.kb_id = self.db.upsert_kb(self.kb_id, title, content, self.kb_tags.get(), int(self.kb_enabled.get()))
        self.refresh_kb()
        messagebox.showinfo("OK", "Saved")

    def kb_delete(self):
        if not self.kb_id: return
        if messagebox.askyesno("确认", "确定删除？"):
            self.db.delete_kb(self.kb_id)
            self.kb_new()
            self.refresh_kb()
