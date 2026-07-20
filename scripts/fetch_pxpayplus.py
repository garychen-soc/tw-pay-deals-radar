#!/usr/bin/env python3
"""全支付 PX Pay Plus 活動抓取 — 後端 JSON API 增量掃描。

全支付官網 (marketing.pxpayplus.com) 是純 CSR SPA，原始 HTML 是空殼，
但活動明細有乾淨的後端 API，且 CORS 允許，伺服器端直接 GET 即可：

    GET https://service.pxpayplus.com/px-advertise/web/activity/detail/{id}
      code == "0000" → 有效活動；data.title / activity_start_time / activity_end_time
      code == "2001" → 查無資料（該 id 不存在）

活動 id 從 1 遞增，連續數個 "2001" 即到末端（2026-07 實測末端 id≈107）。
本腳本僅用 Python 標準庫，已實測可直接執行：

    python scripts/fetch_pxpayplus.py > pxpayplus.json

⚠️ 勿改用 prod-s3.pxpayplus.com/MKT_Event/event{id}.json —— 那是 2024 舊快取，不可靠。
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime
import json
import sys
import urllib.error
import urllib.request

BASE = "https://service.pxpayplus.com/px-advertise/web/activity/detail/{}"
UA = "tw-pay-deals-radar/1.0 (+https://github.com/garychen-soc/tw-pay-deals-radar)"
MAX_ID = 130          # 掃描上限（末端實測約 107，留緩衝）
WORKERS = 12


def fetch_one(cid: int) -> dict | None:
    """抓單一活動 id；無效（code!=0000 或無標題）回 None。"""
    req = urllib.request.Request(BASE.format(cid), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None
    if payload.get("code") != "0000":
        return None
    data = payload.get("data") or {}
    title = (data.get("title") or "").strip()
    if not title:
        return None
    return {
        "id": cid,
        "service": "全支付",
        "title": title,
        "start": data.get("activity_start_time"),
        "end": data.get("activity_end_time"),
    }


def scan(max_id: int = MAX_ID) -> list[dict]:
    """並發掃描 id 1..max_id，回傳所有有效活動。"""
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = pool.map(fetch_one, range(1, max_id + 1))
    return [r for r in results if r]


def not_expired(items: list[dict], today: datetime.date | None = None) -> list[dict]:
    """濾掉結束日早於今日者；日期不明則保留。"""
    today = today or datetime.date.today()
    live = []
    for it in items:
        raw = (it.get("end") or "")[:10].replace("/", "-")
        try:
            if datetime.date.fromisoformat(raw) >= today:
                live.append(it)
        except ValueError:
            live.append(it)
    return live


if __name__ == "__main__":
    everything = scan()
    live = not_expired(everything)
    print(f"# 全支付：掃描 {len(everything)} 筆、未過期 {len(live)} 筆", file=sys.stderr)
    print(json.dumps(live, ensure_ascii=False, indent=2))
