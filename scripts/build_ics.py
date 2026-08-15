#!/usr/bin/env python3
"""由 data/promotions.json 產生可訂閱的行事曆 calendar.ics。

設計決定：**按日期彙總，而非一檔活動一個事件。**
資料實測（2026-08-14）有 88 檔活動同一天（12/31）截止、63 檔 9/30 截止。若一檔一個
事件，Google 日曆會在單日疊出 88 條全天橫幅，把使用者自己的行程整個蓋掉，訂閱等於不能用。
因此同一天截止的活動合併成一個「N 檔優惠今日截止」事件，清單放在事件說明裡。

只輸出「當日起算的未來事件」，且排除已額滿（sold_out）與已結束的活動——過期提醒沒有意義。
輸出是輸入的純函式（以 promotions.json 的 generated_at 當今日基準），重跑不會產生無謂 diff。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SITE_URL = "https://garychen-soc.github.io/tw-pay-deals-radar/"
PRODID = "-//tw-pay-deals-radar//台灣行動支付優惠雷達//ZH-TW"
CAL_NAME = "台灣行動支付優惠雷達"
CAL_DESC = "台灣行動支付／電子支付優惠的截止與開跑提醒，每日自動更新。"

# 事件說明裡最多列幾檔，超過只給總數與網站連結（12/31 那種 88 檔的日子會用到）
MAX_ITEMS_IN_DESCRIPTION = 25


def escape(text: str) -> str:
    """RFC 5545 §3.3.11 文字值跳脫。反斜線必須先處理。"""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold(line: str) -> str:
    """RFC 5545 §3.1 折行：每行上限 75 octet，續行以一個空格開頭。

    長度以 UTF-8 位元組計，且不可切在多位元組字元中間——中文標題若切錯就會變亂碼。
    """
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line
    chunks: list[bytes] = []
    start, limit = 0, 75
    while start < len(data):
        end = min(start + limit, len(data))
        # 往回退到字元邊界（0b10xxxxxx 是 UTF-8 續位元組）
        while start < end < len(data) and (data[end] & 0xC0) == 0x80:
            end -= 1
        chunks.append(data[start:end])
        start = end
        limit = 74  # 續行少一格給開頭的空白
    return "\r\n ".join(c.decode("utf-8") for c in chunks)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def describe(items: list[dict], verb: str) -> str:
    lines = [f"{verb}的優惠共 {len(items)} 檔：", ""]
    for a in items[:MAX_ITEMS_IN_DESCRIPTION]:
        reward = a.get("reward") or ""
        provider = a.get("provider_name") or "優惠"
        title = a.get("title") or "未命名活動"
        lines.append(f"・[{provider}] {title}" + (f" — {reward}" if reward else ""))
    remaining = len(items) - MAX_ITEMS_IN_DESCRIPTION
    if remaining > 0:
        lines.append(f"…另有 {remaining} 檔，請見網站。")
    lines += ["", f"完整清單與篩選：{SITE_URL}", "實際優惠內容以各官方活動辦法為準。"]
    return "\n".join(lines)


def build_events(data: dict, today: date) -> list[dict]:
    activities = data.get("activities") or []
    ending: dict[date, list[dict]] = defaultdict(list)
    starting: dict[date, list[dict]] = defaultdict(list)

    for a in activities:
        if a.get("lifecycle") == "ended" or a.get("quota_status") == "sold_out":
            continue  # 已結束或整檔額滿的活動提醒沒有意義
        end = parse_date(a.get("end_date"))
        if end and end >= today:
            ending[end].append(a)
        start = parse_date(a.get("start_date"))
        if start and start > today:  # 只提醒「還沒開跑」的
            starting[start].append(a)

    events = []
    for day, items in sorted(ending.items()):
        events.append(
            {
                "uid": f"end-{day.isoformat()}@tw-pay-deals-radar",
                "day": day,
                "summary": f"{len(items)} 檔優惠今日截止",
                "description": describe(items, "今日截止"),
                "categories": "截止提醒",
            }
        )
    for day, items in sorted(starting.items()):
        events.append(
            {
                "uid": f"start-{day.isoformat()}@tw-pay-deals-radar",
                "day": day,
                "summary": f"{len(items)} 檔新優惠開跑",
                "description": describe(items, "今日開跑"),
                "categories": "開跑提醒",
            }
        )
    return events


def render(events: list[dict], dtstamp: datetime) -> str:
    stamp = dtstamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape(CAL_NAME)}",
        f"X-WR-CALDESC:{escape(CAL_DESC)}",
        "X-WR-TIMEZONE:Asia/Taipei",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    for ev in events:
        day: date = ev["day"]
        lines += [
            "BEGIN:VEVENT",
            f"UID:{ev['uid']}",
            f"DTSTAMP:{stamp}",
            f"LAST-MODIFIED:{stamp}",
            f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
            # 全天事件的 DTEND 不含當日，需 +1 天
            f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{escape(ev['summary'])}",
            f"DESCRIPTION:{escape(ev['description'])}",
            f"CATEGORIES:{escape(ev['categories'])}",
            f"URL:{SITE_URL}",
            "TRANSP:TRANSPARENT",  # 不佔用忙碌時間
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="由 promotions.json 產生 calendar.ics")
    ap.add_argument("--input", default=str(REPO / "data" / "promotions.json"))
    ap.add_argument("--output", default=str(REPO / "calendar.ics"))
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"找不到資料檔：{src}", file=sys.stderr)
        return 1
    data = json.loads(src.read_text(encoding="utf-8"))

    generated_at = data.get("generated_at")
    try:
        stamp = datetime.fromisoformat(generated_at)
    except (TypeError, ValueError):
        print("generated_at 無法解析，改用目前時間", file=sys.stderr)
        stamp = datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)

    events = build_events(data, stamp.date())
    Path(args.output).write_text(render(events, stamp), encoding="utf-8", newline="")

    ends = sum(1 for e in events if e["uid"].startswith("end-"))
    starts = len(events) - ends
    print(f"已寫入 {args.output}：{len(events)} 個事件（截止 {ends}、開跑 {starts}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
