"""icash Pay 擷取器：2026-11-06 的官方頁面與條款 JSON 為 fixture，不連網。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from payradar.providers.icashpay import (
    IcashPayAdapter,
    json_url,
    parse_cards,
    parse_period,
)
from payradar.transport import FetchFailed, Response

FIXTURES = Path(__file__).parents[1] / "fixtures"
PAGE = "https://www.icashpay.com.tw/event/memberbenefits/"
DATA = "https://www.icashpay.com.tw/event/memberbenefits/memberbenefits-data.json?v=20260326"
TODAY = date(2026, 11, 6)


class FakeHttp:
    def __init__(self, *, json_fails: bool = False) -> None:
        self.calls: list[str] = []
        self.json_fails = json_fails

    def get(self, url: str, **_: Any) -> Response:
        self.calls.append(url)
        if url == PAGE:
            return Response(
                url, url, 200, (FIXTURES / "icashpay_list.html").read_text("utf-8"), "text/html", ""
            )
        if url == DATA:
            if self.json_fails:
                raise FetchFailed("HTTP 500")
            return Response(
                url,
                url,
                200,
                (FIXTURES / "icashpay_data.json").read_text("utf-8"),
                "application/json",
                "",
            )
        raise AssertionError(url)


def adapter(**kwargs: bool) -> tuple[IcashPayAdapter, FakeHttp]:
    http = FakeHttp(**kwargs)
    return IcashPayAdapter(http, today=TODAY), http  # type: ignore[arg-type]


def test_discover_lists_only_live_cards() -> None:
    a, http = adapter()
    stubs = a.discover()
    # 統一精工(06)、星巴克(07) 被 HTML 註解掩起來，不算現役
    assert [s.key for s in stubs] == [
        "01",
        "02",
        "03",
        "04",
        "05",
        "08",
        "09",
        "10",
        "11",
        "12",
        "13",
    ]
    assert len({s.key for s in stubs}) == len(stubs)
    assert http.calls == [PAGE, DATA]
    assert stubs[0].title == "統一超商"
    assert stubs[0].category == "點數回饋"
    assert stubs[0].summary == "使用 icash Pay 消費享 0.5% OPENPOINT 回饋"
    assert all(s.url == PAGE for s in stubs)
    assert all(
        (s.period_start, s.period_end) == (date(2026, 7, 1), date(2026, 12, 31)) for s in stubs
    )
    assert not any("星巴克" in s.title or "統一精工" in s.title for s in stubs)


def test_expired_section_is_dropped() -> None:
    http = FakeHttp()
    a = IcashPayAdapter(http, today=date(2027, 1, 2))  # type: ignore[arg-type]
    assert a.discover() == []


def test_terms_combine_card_text_and_json_clauses() -> None:
    a, _ = adapter()
    stubs = {s.key: s for s in a.discover()}
    cosmed = stubs["02"].detail_text or ""
    for needle in (
        "單筆滿 $500 立折 $50",
        "單筆發票限折乙次",
        "逾期無效，不得申請補發",
        "不與其他行銷活動併用",
    ):
        assert needle in cosmed
    assert a.terms(stubs["02"]).text == cosmed
    # 頁面導覽、「點數折抵」「票夾說明」教學步驟不混入
    for stub in stubs.values():
        assert "按下付款碼" not in (stub.detail_text or "")
        assert "下載註冊" not in (stub.detail_text or "")


def test_image_clause_is_flagged() -> None:
    a, _ = adapter()
    spa = next(s for s in a.discover() if s.key == "13")
    assert (
        "（此處條款為圖片：https://www.icashpay.com.tw/event/memberbenefits/img/memberbenefits_modal13_img.png）"
        in (spa.detail_text or "")
    )


def test_json_failure_keeps_cards_and_records_failure() -> None:
    a, _ = adapter(json_fails=True)
    stubs = a.discover()
    assert len(stubs) == 10
    assert len(a.failures) == 1
    # 沒有 JSON 時標題退回卡片圖片 alt（BEING spa 的 alt 是空的，標題會被丟棄）
    assert {s.title for s in stubs} >= {"統一超商", "聖娜多堡"}
    assert len(a.skipped) == 1


def test_helpers() -> None:
    html = (FIXTURES / "icashpay_list.html").read_text("utf-8")
    assert json_url(html, PAGE) == DATA
    assert len(parse_cards(html)) == 11
    assert parse_period("2026/7/1~12/31") == (date(2026, 7, 1), date(2026, 12, 31))
    assert parse_period("每月") == (None, None)
