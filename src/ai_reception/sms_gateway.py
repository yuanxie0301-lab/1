from __future__ import annotations
import time

class SmsGateway:
    """
    Interface:
      - send_sms(phone, text) -> (ok: bool, status: str, msg_id: str)
    status: 'sent'|'failed'|'pending'
    """
    def __init__(self, mode: str):
        self.mode = (mode or "simulator").lower()

    def send_sms(self, phone: str, text: str):
        phone = (phone or "").strip()
        text = (text or "").strip()
        if self.mode == "off":
            return False, "failed", ""
        # simulator: instant sent
        fake_id = f"sim-{int(time.time()*1000)}"
        return True, "sent", fake_id
