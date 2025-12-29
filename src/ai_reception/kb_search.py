from __future__ import annotations
import re

def _keywords(text: str):
    parts = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{3,}", text or "")
    seen, out = set(), []
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        if len(out) >= 8:
            break
    return out

def pick_kb_context(user_text: str, kb_rows, max_items: int = 4):
    kws = _keywords(user_text)
    scored = []
    for r in kb_rows:
        if not r.get("enabled"):
            continue
        hay = (r.get("title","") + "\n" + r.get("content","") + "\n" + r.get("tags","")).lower()
        score = 0
        for k in kws:
            if k.lower() in hay:
                score += 1
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], str(x[1].get("updated_time",""))))
    out = []
    for _, r in scored[:max_items]:
        out.append({"role": "system", "content": f"知识库：{r.get('title','')}\n{r.get('content','')}".strip()})
    return out
