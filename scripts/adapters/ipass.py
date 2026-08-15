"""一卡通 iPASS MONEY 確定性 adapter：HTML 列表 parse（type=0 分頁）。

官網 Preferential?type=0 是傳統伺服器渲染 HTML（非 SPA），Mozilla UA 可直接抓：
    /Preferential?type=0&page=N  →  每個活動含 Detail 連結 + img alt(標題) + 日期範圍
type=0 為全部；分頁到空頁即末端。額滿字樣在列表標題上才判得到（內文額滿需進 Detail，成本高故從略）。
"""
from __future__ import annotations

import datetime
import re
import subprocess

from . import base

PROVIDER_ID = "ipassmoney"
PROVIDER_NAME = "一卡通 iPASS MONEY"
LIST = "https://www.i-pass.com.tw/Preferential?type=0&page={}"
DETAIL = "https://www.i-pass.com.tw/Preferential/Detail/{}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")
MAX_PAGE = 8

_DATE = re.compile(
    r"(\d{4})/(\d{1,2})/(\d{1,2})\s*[（(]?[日一二三四五六]?[）)]?\s*[~～]\s*"
    r"(\d{4})/(\d{1,2})/(\d{1,2})")
_FULL = re.compile(r"額滿")
_MONTH_FULL = re.compile(r"每月.{0,4}額滿|每月.{0,4}上限")


def _iso(y, m, d):
    try:
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except (ValueError, TypeError):
        return None


def quota_of(title):
    """列表標題含額滿字樣才判；每月額滿=partial、其餘額滿=sold_out。"""
    t = title or ""
    if _MONTH_FULL.search(t):
        return "partial_sold_out", True
    if _FULL.search(t):
        return "sold_out", True
    return "not_marked_full", False


def parse_page(html):
    """從一頁 HTML 抽出 [(id, title, start, end)]。同 id 常出現兩次（圖塊帶標題、
    文字塊帶日期），合併取較完整者，避免先遇到的空日期蓋掉後面的。"""
    found = {}
    for m in re.finditer(r"Preferential/Detail/([A-Za-z0-9]+)", html):
        aid = m.group(1)
        block = html[max(0, m.start() - 600): m.end() + 600]
        tm = re.search(r'(?:alt|title)="([^"]{6,120})"', block)
        title = tm.group(1).strip() if tm else ""
        dm = _DATE.search(re.sub(r"<[^>]+>", " ", block))
        start = end = None
        if dm:
            start = _iso(dm.group(1), dm.group(2), dm.group(3))
            end = _iso(dm.group(4), dm.group(5), dm.group(6))
        prev = found.get(aid)
        if prev:  # 合併另一塊補上 title/date
            title = title or prev[1]
            start = start or prev[2]
            end = end or prev[3]
        found[aid] = (aid, title, start, end)
    return [v for v in found.values() if v[1]]  # 需有 title


def _fetch_page(page):
    """用系統 curl 抓：i-pass 證書鏈在 Python urllib 驗證失敗(CERTIFICATE_VERIFY_FAILED)，
    curl 用系統 CA 正常。"""
    try:
        r = subprocess.run(["curl", "-sL", "-m", "20", "-A", UA, LIST.format(page)],
                           capture_output=True, text=True, timeout=25)
        return r.stdout if r.returncode == 0 and r.stdout else None
    except (subprocess.SubprocessError, OSError):
        return None


def fetch(today=None):
    today = today or datetime.date.today()
    items = {}
    pages_ok = 0
    entry_ok = 0
    for page in range(1, MAX_PAGE + 1):
        html = _fetch_page(page)
        if html is None:
            continue
        entry_ok = 1
        page_items = parse_page(html)
        if not page_items:
            break  # 空頁＝末端
        pages_ok += 1
        for aid, t, s, e in page_items:
            items.setdefault(aid, (aid, t, s, e))

    acts = []
    for aid, title, start, end in items.values():
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
             "extended_ok": pages_ok, "extended_expected": pages_ok or 1,
             "errors": 0 if entry_ok else 1, "live": len(acts)}
    return acts, stats
