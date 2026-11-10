"""網站資料與行事曆：只放事實、UID 穩定、RFC 5545 折行。"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from payradar.emit import _fold, ics, timeline, write
from payradar.models import Activity, Evidence, Offer, Reward, Slot, SourceHealth
from payradar.pipeline import TAIPEI

NOW = datetime(2026, 11, 6, 9, 0, tzinfo=TAIPEI)


def activity(aid: str, **fields: object) -> Activity:
    base: dict[str, object] = {
        "id": aid,
        "provider_id": "pxpayplus",
        "provider_name": "全支付",
        "title": f"活動{aid}",
        "url": f"https://marketing.pxpayplus.com/x?EventId={aid}",
        "period_start": date(2026, 11, 1),
        "period_end": date(2026, 11, 30),
        "observed_at": NOW,
    }
    return Activity.model_validate({**base, **fields})


def slot(start: str, end: str | None = None) -> Slot:
    return Slot(
        start=datetime.fromisoformat(start),
        end=datetime.fromisoformat(end) if end else None,
        kind="range" if end else "opens_at",
        origin="explicit",
        evidence=Evidence(quote="於2026/11/29(日)00:00始開放登錄", url="https://x"),
    )


def test_timeline_order_is_stable_and_deduped() -> None:
    offer = Offer(slots=[slot("2026-11-29T00:00:00+08:00")])
    a = activity("b", offers=[offer])
    b = activity("a", offers=[offer])
    first = timeline([a, b], NOW)
    second = timeline([b, a], NOW)
    assert [r["aid"] for r in first] == [r["aid"] for r in second]
    assert len(timeline([a, a], NOW)) == 1


def test_ics_has_alarmed_slots_and_all_day_deadlines() -> None:
    a = activity(
        "1",
        offers=[
            Offer(
                slots=[slot("2026-11-29T00:00:00+08:00")],
                rewards=[Reward(kind="percent", value=5, unit="%", quote="享5%")],
            )
        ],
    )
    notice = activity("2", is_offer="no")
    text = ics(timeline([a], NOW), [a, notice], NOW)
    assert text.count("BEGIN:VEVENT") == 2, "非優惠公告不放截止日"
    assert "DTSTART:20261128T160000Z" in text
    assert "TRIGGER:-PT10M" in text
    assert "DTSTART;VALUE=DATE:20261130" in text and "DTEND;VALUE=DATE:20261201" in text
    assert "UID:1-end@tw-pay-deals-radar" in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))


def test_fold_keeps_multibyte_characters_whole() -> None:
    folded = _fold("SUMMARY:" + "全支付百大品牌" * 10)
    for part in folded.split("\r\n"):
        assert len(part.encode()) <= 75
        part.encode().decode("utf-8")


def test_write_outputs_facts_only(tmp_path: Path) -> None:
    a = activity("1")
    health = [
        SourceHealth(
            provider_id="pxpayplus", provider_name="全支付", status="ok", entry_url="https://x"
        )
    ]
    counts = write({"pxpayplus": [a]}, health, NOW, out=tmp_path)
    payload = json.loads((tmp_path / "data" / "index.json").read_text("utf-8"))
    assert counts["activities"] == 1
    row = payload["activities"][0]
    assert "content_hash" not in row and "observed_at" not in row
    assert not {"lifecycle", "is_high_return", "quota_status"} & set(row)
    assert (tmp_path / "calendar" / "deals.ics").read_text("utf-8").startswith("BEGIN:VCALENDAR")
    assert (tmp_path / "calendar.ics").read_text("utf-8") == (
        tmp_path / "calendar" / "deals.ics"
    ).read_text("utf-8"), "v1 舊訂閱網址要同步"
