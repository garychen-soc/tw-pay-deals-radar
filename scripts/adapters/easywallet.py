"""悠遊付 easycard 確定性 adapter：HTML 列表 parse。

easywallet.easycard.com.tw/benefit 列表頁 curl 可抓——之前 verify_links 看到的
「502 / The URL you requested was rejected」是 headless 瀏覽器指紋被 WAF 擋的假象，
系統 curl 單次抓完全正常。卡片結構規整：
    <a href="/benefit/content.php?id=NNN" class="slider-card">
      …<p class="title">標題</p><p class="date">2026-08-01 － 2026-11-30</p>
使用者頁面用無 .php 版 /benefit/content?id=NNN（與白名單及既有連結一致）。
"""
from __future__ import annotations

import datetime
import re
import subprocess

from . import base

PROVIDER_ID = "easywallet"
PROVIDER_NAME = "悠遊付"
LIST = "https://easywallet.easycard.com.tw/benefit"
DETAIL = "https://easywallet.easycard.com.tw/benefit/content?id={}"  # 使用者頁：無 .php
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")

_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*[－–—~-]\s*(\d{4}-\d{2}-\d{2})")
_FULL = re.compile(r"額滿")
_MONTH_FULL = re.compile(r"每月.{0,4}額滿|每月.{0,4}上限")


def quota_of(title):
    t = title or ""
    if _MONTH_FULL.search(t):
        return "partial_sold_out", True
    if _FULL.search(t):
        return "sold_out", True
    return "not_marked_full", False


def _fetch(url):
    """curl 抓（easycard 憑證/WAF 對 headless 敏感，系統 curl 正常）。"""
    try:
        r = subprocess.run(["curl", "-sL", "-m", "25", "-A", UA, url],
                           capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 and r.stdout else None
    except (subprocess.SubprocessError, OSError):
        return None


def parse_page(html):
    """從列表 HTML 抽出 [(id, title, start, end)]（同 id 去重）。"""
    found = {}
    for m in re.finditer(r"benefit/content\.php\?id=(\d+)", html):
        aid = m.group(1)
        if aid in found:
            continue
        block = html[m.start(): m.end() + 400]  # card-text-block 在連結之後
        tm = re.search(r'<p class="title">(.*?)</p>', block, re.S)
        title = re.sub(r"<[^>]+>", "", tm.group(1)).strip() if tm else ""
        if not title:
            continue
        start = end = None
        dm = re.search(r'<p class="date">(.*?)</p>', block, re.S)
        if dm:
            d = _DATE.search(dm.group(1))
            if d:
                start, end = d.group(1), d.group(2)
        found[aid] = (aid, title, start, end)
    return list(found.values())


def fetch(today=None):
    today = today or datetime.date.today()
    html = _fetch(LIST)
    entry_ok = 1 if html else 0
    items = parse_page(html) if html else []

    acts = []
    for aid, title, start, end in items:
        if end:
            try:
                if datetime.date.fromisoformat(end) < today:
                    continue  # 過期剔除
            except ValueError:
                pass
        quota, quota_ev = quota_of(title)
        reward, high = base.extract_reward(title)
        url = DETAIL.format(aid)
        acts.append(base.build_activity(
            id=f"{PROVIDER_ID}-{aid}", provider_id=PROVIDER_ID, provider_name=PROVIDER_NAME,
            title=title, url=url, start_date=start, end_date=end,
            lifecycle=base.lifecycle_of(start, end, today),
            quota_status=quota, quota_evidence_complete=quota_ev,
            reward=reward, is_high_return=high, source="official",
            date_confidence="high" if (start and end) else "medium",
            evidence=[base.make_evidence(url, title, "list")]))

    stats = {"provider": PROVIDER_NAME, "official_ok": entry_ok, "official_expected": 1,
             "extended_ok": 1 if entry_ok else 0, "extended_expected": 1,
             "errors": 0 if entry_ok else 1, "live": len(acts)}
    return acts, stats
