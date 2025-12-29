from __future__ import annotations
import json, urllib.request
from dataclasses import dataclass

@dataclass
class LLMConfig:
    mode: str
    ollama_base_url: str
    ollama_model: str
    cloud_base_url: str
    cloud_api_key: str
    cloud_model: str

class LLMRouter:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    def chat(self, messages: list[dict[str,str]]):
        mode = (self.cfg.mode or "local_first").lower()
        if mode == "off":
            return False, ""
        if mode == "cloud_first":
            ok, out = self._cloud_chat(messages)
            if ok: return True, out
            ok, out = self._ollama_chat(messages)
            if ok: return True, out
            return False, ""
        else:
            ok, out = self._ollama_chat(messages)
            if ok: return True, out
            ok, out = self._cloud_chat(messages)
            if ok: return True, out
            return False, ""

    def _ollama_chat(self, messages):
        base = (self.cfg.ollama_base_url or "").rstrip("/")
        if not base:
            return False, ""
        url = f"{base}/api/chat"
        payload = {"model": self.cfg.ollama_model or "llama3.1:8b", "messages": messages, "stream": False}
        try:
            req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                         headers={"Content-Type":"application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = ((data.get("message") or {}).get("content") or "").strip()
            return (True, content) if content else (False, "")
        except Exception:
            return False, ""

    def _cloud_chat(self, messages):
        key = (self.cfg.cloud_api_key or "").strip()
        base = (self.cfg.cloud_base_url or "").rstrip("/")
        if not key or not base:
            return False, ""
        url = f"{base}/v1/chat/completions"
        payload = {"model": self.cfg.cloud_model or "gpt-4o-mini", "messages": messages, "temperature": 0.4}
        try:
            req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                         headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            ch = (data.get("choices") or [])
            if not ch: return False, ""
            msg = ((ch[0].get("message") or {}).get("content") or "").strip()
            return (True, msg) if msg else (False, "")
        except Exception:
            return False, ""
