from __future__ import annotations
import tkinter as tk
from tkinter import messagebox
from .storage import user_data_dir
from .db import DB
from .ui import RootUI

def run_app():
    root = tk.Tk()
    root.title("AIReception V4")
    root.geometry("1320x780")
    root.minsize(1020, 660)

    data_dir = user_data_dir()
    db = DB(data_dir / "app.db")

    try:
        ui = RootUI(root, db)
        ui.pack(fill="both", expand=True)
        root.protocol("WM_DELETE_WINDOW", lambda: (db.close(), root.destroy()))
        root.mainloop()
    except Exception as e:
        messagebox.showerror("Error", f"启动失败：\n{e}")
        try: db.close()
        except Exception: pass
        raise
