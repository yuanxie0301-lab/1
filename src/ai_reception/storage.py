from __future__ import annotations
import os
from pathlib import Path
import sys

def app_root() -> Path:
    # repo root for dev, or exe folder for packaged
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]

def user_data_dir() -> Path:
    # Always local (no cloud). Prefer Windows LocalAppData for installed apps.
    # This also ensures uninstall won't delete your knowledge base automatically.
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(app_root())
        d = Path(base) / "AIReception" / "user_data"
    else:
        d = app_root() / "user_data"
    d.mkdir(parents=True, exist_ok=True)
    return d
