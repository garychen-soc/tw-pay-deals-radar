"""台灣Pay 確定性 adapter：TF020109 API → 帶 evidence + 額滿判定的活動。

官網是 SPA、活動內容是圖，但有後端 JSON API（伺服器端可直接 POST，實測 2026-08 有效）：
    POST https://www.taiwanpay.com.tw/tpay/v1.0.0/950/taiwanpayfapi/TF02/TF020109
    body 必須是 {"body":{}}（加 header 欄位反而回空）→ body.recommendedCampaigns[]
    每筆：systemSeq / title / startDate / endDate / paymentType
額滿判定確定性做（title 含「(活動額滿)」=整檔 sold_out、「每月額滿」=partial），
使用者頁面 url 必含 fisc- 前綴（/tpay/ 會 404）。
"""
from __future__ import annotations

import datetime
import json
import re
import urllib.error
import urllib.request

from . import base

PROVIDER_ID = "taiwanpay"
PROVIDER_NAME = "台灣Pay"
API = "https://www.taiwanpay.com.tw/tpay/v1.0.0/950/taiwanpayfapi/TF02/TF020109"
PAGE = "https://www.taiwanpay.com.tw/fisc-tpay/news/event/{}"  # 必含 fisc-；/tpay/ 會 404
UA = "Mozilla/5.0 (Macintosh) tw-pay-deals-radar/1.0"

_FULL = re.compile(r"[（(【]\s*活動額滿\s*[）)】]")
_MONTH_FULL = re.compile(r"每月額滿")


def quota_of(title):
    """回傳 (quota_status, quota_evidence_complete)。每月額滿=partial、活動額滿=整檔 sold_out。"""
    t = title or ""
    if _MONTH_FULL.search(t):
        return "partial_sold_out", True
    if _FULL.search(t):
        return "sold_out", True
    return "not_marked_full", False


def fetch(today=None):
    today = today or datetime.date.today()
    req = urllib.request.Request(
        API, data=json.dumps({"body": {}}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            payload = json.loads(r.read().decode("utf-8"))
        camps = (payload.get("body") or {}).get("recommendedCampaigns") or []
        entry_ok = 1
    except (urllib.error.URLError, ValueError, TimeoutError, OSError):
        camps, entry_ok = [], 0

    acts = []
    for c in camps:
        seq = c.get("systemSeq")
        title = (c.get("title") or "").strip()
        if not seq or not title:
            continue
        start = base.iso_date(c.get("startDate"))
        end = base.iso_date(c.get("endDate"))
        if end and datetime.date.fromisoformat(end) < today:
            continue  # 過期剔除
        quota, quota_ev = quota_of(title)
        reward, high = base.extract_reward(title)
        page = PAGE.format(seq)
        acts.append(base.build_activity(
            id=f"{PROVIDER_ID}-{seq}", provider_id=PROVIDER_ID, provider_name=PROVIDER_NAME,
            title=title, url=page, start_date=start, end_date=end,
            lifecycle=base.lifecycle_of(start, end, today),
            quota_status=quota, quota_evidence_complete=quota_ev,
            reward=reward, is_high_return=high, source="api",
            date_confidence="high" if (start and end) else "medium",
            evidence=[base.make_evidence(page, title, "activity_page")]))

    stats = {"provider": PROVIDER_NAME, "official_ok": entry_ok, "official_expected": 1,
             "extended_ok": len(camps) if entry_ok else 0,
             "extended_expected": len(camps) if entry_ok else 1,
             "errors": 0 if entry_ok else 1, "live": len(acts)}
    return acts, stats
