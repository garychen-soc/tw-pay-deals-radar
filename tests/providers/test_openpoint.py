"""OPEN 錢包擷取器：fixture 是 2026-11-06 的 ``/cdn/index.html`` 原檔，不連網。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from payradar.models import Stub
from payradar.providers.base import ExternalTerms
from payradar.providers.openpoint import OpenPointAdapter, parse_range
from payradar.transport import Response

FIXTURES = Path(__file__).parents[1] / "fixtures"
TODAY = date(2026, 11, 6)


class FakeHttp:
    def __init__(self, html: str | None = None) -> None:
        self.html = html if html is not None else (FIXTURES / "openpoint_index.html").read_text()
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> Response:
        self.calls.append(url)
        return Response(url, url, 200, self.html, "text/html", "")


def adapter(html: str | None = None, today: date = TODAY) -> OpenPointAdapter:
    return OpenPointAdapter(FakeHttp(html), today=today)  # type: ignore[arg-type]


def test_discover_filters_forever_tutorial_and_dedupes() -> None:
    a = adapter()
    stubs = a.discover()

    assert [s.title for s in stubs] == [
        "uniopen聯名卡icash2.0自動加值回饋10%",
        "icash Pay週日最好Pay",
        "icash Pay綁uniopen聯名卡天天最高11%",
    ]
    assert len({s.key for s in stubs}) == 3
    assert all(s.category == "累積點數" for s in stubs), "分類頁籤優先於推薦"
    assert stubs[1].period_start == date(2026, 7, 1)
    assert stubs[1].period_end == date(2026, 12, 27)
    assert stubs[0].url == (
        "https://www.openpoint.com.tw/cdn/event.html"
        "?tp=collect_point&code=2026062553eb751b80254728a9c215c234036fb0"
    )
    skipped = " ".join(a.skipped)
    assert "點數折抵代收/售" in skipped and "影片教學" in skipped
    assert not any("停機" in s.title for s in stubs)


def test_expired_not_listed() -> None:
    stubs = adapter(today=date(2026, 12, 28)).discover()
    titles = [s.title for s in stubs]
    assert "icash Pay週日最好Pay" not in titles  # 12/27 結束
    assert "icash Pay綁uniopen聯名卡天天最高11%" in titles


def test_image_only_description_is_external_terms() -> None:
    a = adapter()
    stub = a.discover()[1]
    with pytest.raises(ExternalTerms) as caught:
        a.terms(stub)
    assert caught.value.target == "https://www.icashpay.com.tw/advertMessage/view/id/2284"


def test_text_description_becomes_terms() -> None:
    html = """<script>var ptc = {"CollectPointList":[{"Code":"c1","Title":"集點","URL":"",
    "DateRange":"活動時間：2026/11/01-2026/11/30",
    "Description":"\\u003cp\\u003e每滿100元贈1點\\u003c/p\\u003e"}]};
    var imagePath = 'x';</script>"""
    a = adapter(html)
    stubs = a.discover()
    assert len(stubs) == 1
    assert a.terms(stubs[0]).text == "每滿100元贈1點"


def test_removed_activity_raises() -> None:
    a = adapter()
    with pytest.raises(ValueError, match="已從官網資料移除"):
        a.terms(Stub(provider_id="openpoint", key="gone", title="t", url="https://x"))


def test_parse_range() -> None:
    assert parse_range("活動時間：2026/10/28-2026/10/31") == (
        date(2026, 10, 28),
        date(2026, 10, 31),
    )
    assert parse_range("") == (None, None)
