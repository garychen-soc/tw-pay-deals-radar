"""adapter 共用工具：日期正規化、reward 萃取、evidence 產生、活動組裝。

全部為純函式，方便單元測試——這正是 C 架構的重點：把「知識」固化進可測代碼，
而非每天讓 AI 重新判斷。
"""
from __future__ import annotations

import datetime
import re


def iso_date(v):
    """'2026/04/15 00:00' / '2026-04-15' → '2026-04-15'；無法解析回 None。"""
    if not v:
        return None
    s = str(v)[:10].replace("/", "-")
    try:
        datetime.date.fromisoformat(s)
        return s
    except ValueError:
        return None


def lifecycle_of(start, end, today=None):
    """start 在未來→upcoming；否則 active（過期由 adapter 於抓取時剔除）。"""
    today = today or datetime.date.today()
    s = iso_date(start)
    if s and datetime.date.fromisoformat(s) > today:
        return "upcoming"
    return "active"


_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_FIXED = re.compile(r"(?:折|現折|回饋|送|折抵)\s*\$?(\d{2,5})\s*元")


def extract_reward(title):
    """從標題萃取 (顯示字串, is_high_return)。%≥10 或固定≥100 元＝高回饋。"""
    pcts = [float(x) for x in _PCT.findall(title or "")]
    if pcts:
        m = max(pcts)
        return (f"最高{m:g}%" if len(pcts) > 1 else f"{m:g}%", m >= 10)
    fx = _FIXED.search(title or "")
    if fx:
        amt = int(fx.group(1))
        return (f"${amt}", amt >= 100)
    return ("", False)


def make_evidence(source_url, excerpt, kind="activity_page", observed_at=None):
    """產生一條證據：判定所依據的官方來源 + 原文摘錄 + 觀察時間。"""
    return {
        "source_url": source_url,
        "excerpt": (excerpt or "").strip()[:120],
        "observed_at": observed_at or datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "kind": kind,
    }


def build_activity(*, id, provider_id, provider_name, title, url,
                   start_date=None, end_date=None, channel=None,
                   lifecycle="active", quota_status="not_marked_full",
                   reward="", is_high_return=False, insights=None, source="api",
                   date_confidence="high", review_required=False,
                   quota_evidence_complete=False, evidence=None):
    """組裝一筆符合 promotions.json schema 的活動 dict。"""
    return {
        "id": id, "provider_id": provider_id, "provider_name": provider_name,
        "title": title, "channel": channel or provider_name, "url": url,
        "start_date": start_date, "end_date": end_date,
        "lifecycle": lifecycle, "quota_status": quota_status,
        "reward": reward, "is_high_return": is_high_return,
        "insights": insights or [], "source": source,
        "date_confidence": date_confidence, "review_required": review_required,
        "quota_evidence_complete": quota_evidence_complete,
        "evidence": evidence or [],
    }
