"""全支付 確定性 adapter：後端 API 掃描 → 帶 evidence 的活動。

全支付官網是 CSR SPA，但活動明細有乾淨後端 API（伺服器端直接 GET 即可，實測 2026-08 有效）：
    GET https://service.pxpayplus.com/px-advertise/web/activity/detail/{id}
    code=="0000" 有效、"2001" 查無、其餘視為錯誤。
id 1 遞增掃描；每筆產生 evidence（來源 API + 標題摘錄），確定性、不靠 AI。
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime
import json
import urllib.error
import urllib.request

from . import base

PROVIDER_ID = "pxpayplus"
PROVIDER_NAME = "全支付"
DETAIL = "https://service.pxpayplus.com/px-advertise/web/activity/detail/{}"
PAGE = "https://marketing.pxpayplus.com/pxplus_marketing_page/activity_content_page?EventId={}"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) tw-pay-deals-radar/1.0"
MAX_ID = 130
WORKERS = 12


def _fetch_one(cid):
    """回傳 ('ok', dict) / ('empty', cid) / ('err', cid)——區分查無與抓取失敗。"""
    req = urllib.request.Request(DETAIL.format(cid), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            p = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError, OSError):
        return ("err", cid)
    code = p.get("code")
    if code == "2001":
        return ("empty", cid)
    if code != "0000":
        return ("err", cid)
    data = p.get("data") or {}
    title = (data.get("title") or "").strip()
    if not title:
        return ("empty", cid)
    return ("ok", {"id": cid, "title": title,
                   "start": data.get("activity_start_time"),
                   "end": data.get("activity_end_time")})


def fetch(today=None):
    """回傳 (activities, stats)。activities 符合 schema、帶 evidence；過期已剔除。"""
    today = today or datetime.date.today()
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(_fetch_one, range(1, MAX_ID + 1)))
    ok = [r[1] for r in results if r[0] == "ok"]
    errors = sum(1 for r in results if r[0] == "err")

    acts = []
    for it in ok:
        end = base.iso_date(it["end"])
        if end and datetime.date.fromisoformat(end) < today:
            continue  # 過期剔除
        start = base.iso_date(it["start"])
        reward, high = base.extract_reward(it["title"])
        acts.append(base.build_activity(
            id=f"{PROVIDER_ID}-{it['id']}", provider_id=PROVIDER_ID,
            provider_name=PROVIDER_NAME, title=it["title"], url=PAGE.format(it["id"]),
            start_date=start, end_date=end,
            lifecycle=base.lifecycle_of(start, end, today),
            reward=reward, is_high_return=high, source="api",
            date_confidence="high" if (start and end) else "medium",
            evidence=[base.make_evidence(DETAIL.format(it["id"]), it["title"], "activity_page")],
        ))
    # 官方入口：整支 API 是否連得上（有任一成功即入口 OK）；延伸：每筆 detail 一次檢查
    entry_ok = 1 if errors < MAX_ID else 0
    stats = {"provider": PROVIDER_NAME, "official_ok": entry_ok, "official_expected": 1,
             "extended_ok": len(ok), "extended_expected": len(ok) + errors,
             "errors": errors, "live": len(acts)}
    return acts, stats
