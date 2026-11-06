"""橘子支付擷取器：以 2026-11-06 取得的官方 API 回應為 fixture，不連網。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from payradar.models import Stub
from payradar.providers.gamapay import GamaPayAdapter, parse_period
from payradar.transport import Response

FIXTURES = Path(__file__).parents[1] / "fixtures"
TODAY = date(2026, 11, 6)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


class FakeHttp:
    def __init__(self, missing: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.missing = missing or set()

    def get(self, url: str, **_: Any) -> Response:
        self.calls.append(url)
        if url.endswith("/api/NewsList"):
            body = fixture("gamapay_newslist.json")
        else:
            news_id = url.rsplit("/", 1)[1]
            if news_id in self.missing:
                raise RuntimeError("boom")
            body = fixture(f"gamapay_news_{news_id}.json")
        return Response(url, url, 200, body, "application/json", "")


def test_discover_only_recent_activity_posts() -> None:
    http = FakeHttp()
    adapter = GamaPayAdapter(http, today=TODAY)  # type: ignore[arg-type]
    stubs = adapter.discover()

    assert [s.key for s in stubs] == ["4991", "4977", "4936", "4909"]
    assert len({s.key for s in stubs}) == len(stubs)
    assert stubs[0].title.startswith("【7-ELEVEN x 橘子支付】")
    assert all(s.url == f"https://www.gamapay.com.tw/news.html?id={s.key}" for s in stubs)
    assert not any("維護" in s.title for s in stubs), "Type 2 系統維護不收"
    # 只抓活動貼文的明細，不抓維護公告
    assert sorted(u.rsplit("/", 1)[1] for u in http.calls[1:]) == ["4909", "4936", "4977", "4991"]


def test_discover_periods_only_when_explicit() -> None:
    stubs = GamaPayAdapter(FakeHttp(), today=TODAY).discover()  # type: ignore[arg-type]
    by_key = {s.key: s for s in stubs}
    assert by_key["4991"].period_start == date(2026, 11, 4)
    assert by_key["4991"].period_end == date(2026, 11, 9)
    assert by_key["4977"].period_end == date(2026, 12, 31)
    assert by_key["4936"].period_end is None, "民國年起日、沒有迄日：不推測"
    assert by_key["4909"].period_end is None


def test_expired_by_text_is_dropped() -> None:
    adapter = GamaPayAdapter(FakeHttp(), today=date(2026, 11, 10))  # type: ignore[arg-type]
    keys = [s.key for s in adapter.discover()]
    assert "4991" not in keys, "活動內文寫明到 11/9，11/10 就該過期"
    assert "4977" in keys


def test_old_posts_and_winner_lists_not_listed() -> None:
    stubs = GamaPayAdapter(FakeHttp(), today=TODAY).discover()  # type: ignore[arg-type]
    assert all("名單" not in s.title for s in stubs)
    assert "4864" not in {s.key for s in stubs}


def test_single_detail_failure_is_recorded() -> None:
    adapter = GamaPayAdapter(FakeHttp(missing={"4977"}), today=TODAY)  # type: ignore[arg-type]
    keys = [s.key for s in adapter.discover()]
    assert "4977" not in keys and "4991" in keys
    assert len(adapter.failures) == 1


def test_terms_from_body_without_images_or_markup() -> None:
    adapter = GamaPayAdapter(FakeHttp(), today=TODAY)  # type: ignore[arg-type]
    stubs = {s.key: s for s in adapter.discover()}
    text = stubs["4991"].detail_text or ""
    assert "單筆滿$1111" in text and "回饋總筆數" in text
    assert "<p>" not in text and "&nbsp;" not in text and "base64" not in text

    fetched = adapter.terms(Stub(provider_id="gamapay", key="4977", title="t", url="https://x"))
    assert "6折優惠" in fetched.text


def test_parse_period_variants() -> None:
    assert parse_period("2026/11/4~11/9 單筆") == (date(2026, 11, 4), date(2026, 11, 9))
    assert parse_period("2026/12/20~1/5") == (date(2026, 12, 20), date(2027, 1, 5))
    assert parse_period("即日起至 2026 年 12 月 31 日") == (None, date(2026, 12, 31))
    assert parse_period("2026/07/01 起") == (None, None)
    assert json.loads(fixture("gamapay_news_4909.json"))["Data"]["Title"]
