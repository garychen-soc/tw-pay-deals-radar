"""從 state 產生網站資料。

``web/public/data/index.json``
    全部活動（精簡欄位）、來源健康、涵蓋缺口、登錄／領券時點。前端以「今天」計算
    進行中、即將開始、即將截止、高回饋、現在是否額滿。
``web/public/calendar/deals.ics``（另存一份到 v1 舊網址 ``calendar.ics``）
    可訂閱的行事曆：登錄／領券開放時點（精確到分鐘、開放前 10 分鐘提醒）＋
    各優惠的截止日（全天事件，不提醒）。

只放事實：「今天是否進行中」「是否即將截止」這類隨時間變動的狀態由前端計算。
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import Activity, SourceHealth

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "public"
SCHEMA_VERSION = 2
HORIZON = timedelta(days=120)
LOOKBACK = timedelta(days=2)
STALE_NOTE = "⚠️ 官方頁本輪讀取失敗，以下為上一輪資料，請以活動頁為準"
UID_DOMAIN = "tw-pay-deals-radar"

# 與 docs/coverage.md 同步：前端「來源狀態」分頁顯示
COVERAGE_GAPS = [
    {"provider": "全盈+PAY", "note": "官方懶人包月底才發布，當月新開的活動要等下個月才收得到"},
    {"provider": "全支付", "note": "額滿公告只在 App 小鈴鐺，條款不會寫已額滿"},
    {"provider": "一卡通 iPASS MONEY", "note": "部分活動條款在合作銀行網站，未抽取"},
    {"provider": "街口", "note": "部分活動連到合作方網站，未抽取"},
    {"provider": "橘子支付", "note": "只看近 210 天的活動貼文"},
    {"provider": "OPEN 錢包", "note": "活動都是 icash Pay 活動（已收錄於 icash Pay），不重複列出"},
    {"provider": "全家 FamiPay", "note": "沒有統一活動列表，未收錄"},
    {"provider": "LINE Pay", "note": "官網沒有具體優惠活動，未收錄"},
]

_DROP = {"content_hash", "observed_at"}


def timeline(activities: list[Activity], generated: datetime) -> list[dict[str, Any]]:
    """登錄／領券時點（與刷卡登錄雷達相同的列結構）。"""
    rows: list[dict[str, Any]] = []
    for activity in activities:
        for index, offer in enumerate(activity.offers):
            for slot in offer.slots:
                anchor = slot.start or slot.end
                if anchor is None:
                    continue
                last = slot.end or slot.start or anchor
                if last < generated - LOOKBACK or anchor > generated + HORIZON:
                    continue
                rows.append(
                    {
                        "aid": activity.id,
                        "provider_id": activity.provider_id,
                        "provider": activity.provider_name,
                        "title": activity.title,
                        "sub": offer.name,
                        "sub_index": index,
                        "start": slot.start.isoformat() if slot.start else None,
                        "end": slot.end.isoformat() if slot.end else None,
                        "kind": slot.kind,
                        "precision": slot.precision,
                        "shifted": slot.holiday_shifted,
                        "note": slot.note,
                        "quote": slot.evidence.quote,
                        "url": activity.url,
                        "stale": activity.stale,
                    }
                )
    # 排序鍵含 aid／sub_index：ICS 的 UID 不能取決於輸入順序
    rows.sort(
        key=lambda r: (
            r["start"] or r["end"],
            r["provider_id"],
            r["title"],
            r["aid"],
            r["sub_index"],
        )
    )
    seen: set[tuple[Any, ...]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = (row["provider_id"], row["title"], row["sub"], row["start"], row["end"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def counts(activities: list[Activity]) -> dict[str, int]:
    offers = [a for a in activities if a.is_offer != "no"]
    return {
        "activities": len(activities),
        "offers": len(offers),
        "with_rewards": sum(1 for a in offers if any(o.rewards for o in a.offers)),
        "registration_required": sum(1 for a in offers if a.registration == "required"),
        "sold_out_statements": sum(
            1 for a in offers if a.title_sold_out or any(o.sold_out for o in a.offers)
        ),
        "external_or_image": sum(1 for a in offers if a.terms_source in ("external", "image")),
        "stale": sum(1 for a in activities if a.stale),
    }


def write(
    activities: dict[str, list[Activity]],
    health: list[SourceHealth],
    generated: datetime,
    *,
    out: Path = WEB,
) -> dict[str, int]:
    every = [a for items in activities.values() for a in items]
    every.sort(key=lambda a: (a.period_end or date.max, a.provider_id, a.title, a.id))
    rows = timeline(every, generated)
    index = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated.isoformat(),
        "timezone": "Asia/Taipei",
        "counts": counts(every),
        "sources": [h.model_dump(mode="json") for h in health],
        "coverage_gaps": COVERAGE_GAPS,
        "activities": [a.model_dump(mode="json", exclude=_DROP) for a in every],
        "timeline": rows,
    }
    (out / "data").mkdir(parents=True, exist_ok=True)
    _dump(out / "data" / "index.json", index)
    (out / "calendar").mkdir(parents=True, exist_ok=True)
    calendar = ics(rows, every, generated)
    (out / "calendar" / "deals.ics").write_text(calendar, encoding="utf-8", newline="")
    # v1 的訂閱網址是根目錄 calendar.ics：同步輸出一份，已訂閱的人不會在切換後斷掉
    (out / "calendar.ics").write_text(calendar, encoding="utf-8", newline="")
    return index["counts"]  # type: ignore[return-value]


def _dump(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )


# ---- iCalendar ----------------------------------------------------------------


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545：每行不超過 75 octets，續行以空白開頭。"""
    if len(line.encode("utf-8")) <= 75:
        return line
    parts: list[str] = []
    current = b""
    for char in line:
        encoded = char.encode("utf-8")
        if len(current) + len(encoded) > (75 if not parts else 74):
            parts.append(current.decode("utf-8"))
            current = b""
        current += encoded
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def _utc(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _reward_line(activity: Activity) -> str:
    chips: list[str] = []
    for offer in activity.offers:
        for reward in offer.rewards:
            if reward.value is None:
                continue
            value = f"{reward.value:g}"
            chips.append(f"{value}%" if reward.unit == "%" else f"{value}{reward.unit}")
    return "回饋：" + "、".join(dict.fromkeys(chips)) if chips else ""


def ics(rows: list[dict[str, Any]], activities: list[Activity], generated: datetime) -> str:
    stamp = generated.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{UID_DOMAIN}//v2//ZH-TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:台灣支付優惠雷達",
        "X-WR-TIMEZONE:Asia/Taipei",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    for row in rows:
        if not row["start"] or row["precision"] != "minute":
            continue
        title = row["title"] + (f"｜{row['sub']}" if row["sub"] else "")
        description = "\n".join(
            filter(
                None,
                [
                    STALE_NOTE if row["stale"] else "",
                    f"原文：{row['quote']}",
                    row["note"],
                    f"截止：{row['end'][:16].replace('T', ' ')}" if row["end"] else "截止：未公告",
                    f"活動頁：{row['url']}",
                ],
            )
        )
        uid = f"{row['aid']}-{row['sub_index']}-{_utc(row['start'])}@{UID_DOMAIN}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_utc(row['start'])}",
            "DURATION:PT15M",
            "SUMMARY:" + _escape("登錄／領券｜" + row["provider"] + "｜" + title),
            f"DESCRIPTION:{_escape(description)}",
            f"URL:{row['url']}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "DESCRIPTION:" + _escape("即將開放：" + title),
            "TRIGGER:-PT10M",
            "END:VALARM",
            "END:VEVENT",
        ]
    today = generated.date()
    for activity in activities:
        end = activity.period_end
        if end is None or activity.is_offer == "no" or end < today or end > today + HORIZON:
            continue
        description = "\n".join(
            filter(
                None,
                [
                    STALE_NOTE if activity.stale else "",
                    _reward_line(activity),
                    f"活動頁：{activity.url}",
                ],
            )
        )
        lines += [
            "BEGIN:VEVENT",
            f"UID:{activity.id}-end@{UID_DOMAIN}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{end:%Y%m%d}",
            f"DTEND;VALUE=DATE:{end + timedelta(days=1):%Y%m%d}",
            "SUMMARY:" + _escape(f"截止｜{activity.provider_name}｜{activity.title}"),
            f"DESCRIPTION:{_escape(description)}",
            f"URL:{activity.url}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
