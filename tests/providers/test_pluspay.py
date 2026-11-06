"""全盈+PAY 擷取器：fixture 是 2026-11-06 官網月懶人包（只留內容容器）與 Promotion.csv。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from payradar.providers.pluspay import (
    PlusPayAdapter,
    catalog_months,
    clean_heading,
    parse_month,
    parse_period,
)
from payradar.transport import FetchFailed, Response

FIXTURES = Path(__file__).parents[1] / "fixtures"
TODAY = date(2026, 11, 6)
AVAILABLE = {"202609", "202608", "202607", "202606"}


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> Response:
        self.calls.append(url)
        if url.endswith("/csv/Promotion.csv"):
            return Response(url, url, 200, fixture("pluspay_promotion.csv"), "text/csv", "")
        month = url.rsplit("/", 1)[1].removesuffix("Events")
        if month not in AVAILABLE:
            raise FetchFailed(f"404 {url}")
        return Response(url, url, 200, fixture(f"pluspay_{month}Events.html"), "text/html", "")


def adapter(today: date = TODAY) -> PlusPayAdapter:
    return PlusPayAdapter(FakeHttp(), today=today)  # type: ignore[arg-type]


def test_catalog_months_newest_first_and_skips_non_monthly() -> None:
    months = catalog_months(fixture("pluspay_promotion.csv"))
    assert months[:3] == ["202609", "202608", "202607"]
    assert months == sorted(months, reverse=True)
    assert all(len(m) == 6 for m in months), "2023常態活動懶人包不是月頁"


def test_discover_live_activities_dedup_and_titles() -> None:
    a = adapter()
    stubs = a.discover()
    titles = [s.title for s in stubs]

    assert 10 <= len(stubs) <= 25
    assert len({s.key for s in stubs}) == len(stubs)
    assert all(s.period_end and s.period_end >= TODAY for s in stubs), "過期不列"
    assert "全盈+PAY綁定國泰世華信用卡💳小樹點(信用卡)帳單折抵，最高8%回饋" in titles
    assert "東南亞好物一站購足🛒" in titles
    assert not any(t.startswith(("一、", "二、", "【")) for t in titles), "標題已去編號與外框"
    assert not any("信貸" in t for t in titles), "貸款廣告不是支付優惠"
    assert any("信貸" in s for s in a.skipped)
    # 只有 4 個月頁有 fixture；其餘 2 個（202605、202604）記為失敗但不中斷
    assert len(a.failures) == 2


def test_newest_month_wins_and_urls() -> None:
    stubs = adapter().discover()
    cathay = next(s for s in stubs if "小樹點" in s.title)
    assert cathay.url == "https://www.pluspay.com.tw/Campaign/Promotion/202609Events"
    assert (cathay.period_start, cathay.period_end) == (date(2026, 9, 1), date(2026, 12, 31))
    unicard = next(s for s in stubs if "Unicard" in s.title)
    assert unicard.url.endswith("202607Events")
    assert all(s.url.startswith("https://www.pluspay.com.tw/Campaign/Promotion/") for s in stubs)


def test_terms_scoped_to_notice_block() -> None:
    stubs = adapter().discover()
    cathay = next(s for s in stubs if "小樹點" in s.title)
    text = cathay.detail_text or ""
    assert "活動時間:2026/09/01 - 2026/12/31" in text
    assert "小樹點" in text and len(text) > 1500
    # 不含導覽、頁尾，也不含相鄰活動的內文
    assert "最新公告" not in text and "合作通路" not in text
    assert "東南亞好物" not in text
    assert "<li" not in text and "&nbsp;" not in text
    assert adapter().terms(cathay).text == text


def test_expired_dropped_when_today_advances() -> None:
    late = adapter(date(2027, 1, 15)).discover()
    titles = [s.title for s in late]
    assert "東南亞好物一站購足🛒" not in titles  # 12/31 結束
    assert any("台新卡" in t for t in titles)  # 到 2027/03/31


def test_parse_period_variants() -> None:
    assert parse_period("活動時間：2026/09/12 20:00 - 2026/10/30") == (
        date(2026, 9, 12),
        date(2026, 10, 30),
    )
    assert parse_period("活動時間：即日起 - 2026/12/31") == (None, date(2026, 12, 31))
    assert parse_period("活動時間：2026/09/05") == (date(2026, 9, 5), date(2026, 9, 5))
    assert parse_period("活動時間：2026/07/01 - 2026/09/30 (台灣時間)")[1] == date(2026, 9, 30)
    assert parse_period("活動時間：全年") == (None, None)


def test_clean_heading_and_month_parse() -> None:
    assert clean_heading("一、嘟粉專享🤩 全盈+PAY隨買會員日") == "嘟粉專享🤩 全盈+PAY隨買會員日"
    assert clean_heading("三、【連結第一銀行抽萬元mo幣】") == "連結第一銀行抽萬元mo幣"
    rows = parse_month(fixture("pluspay_202609Events.html"))
    assert len(rows) == 9
    assert rows[0][0].startswith("嘟粉專享")
