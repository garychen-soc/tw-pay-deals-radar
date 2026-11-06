"""街口支付擷取器：2026-11-06 的官方 RSC 回應為 fixture，不連網。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from payradar.models import Stub
from payradar.providers.base import ExternalTerms
from payradar.providers.jkopay import (
    JkopayAdapter,
    detail_text,
    external_target,
    parse_cards,
    parse_period,
)
from payradar.transport import FetchFailed, Response

FIXTURES = Path(__file__).parents[1] / "fixtures"
TODAY = date(2026, 11, 6)
LIST_URL = "https://mkt.jkopay.com/zh-TW/campaign/newevent"
HIDDEN = json.loads((FIXTURES / "jkopay_hidden_titles.json").read_text("utf-8"))


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> Response:
        self.calls.append(url)
        if url == LIST_URL:
            body = fixture("jkopay_list.html")
        elif url.endswith("/campaign/jkopayebill"):
            body = fixture("jkopay_detail.html")
        elif url.endswith("/event/charity202611"):
            body = fixture("jkopay_detail_charity.html")
        elif "/".join(url.split("/")[-2:]) in HIDDEN:
            # 隱藏活動頁只用得到 <head><title>，fixture 只留標題
            body = (
                f"<html><head><title>{HIDDEN['/'.join(url.split('/')[-2:])]}</title></head></html>"
            )
        else:
            raise FetchFailed(f"抓取失敗 {url}：HTTP 404")
        return Response(url, url, 200, body, "text/html", "")


def adapter(today: date = TODAY) -> tuple[JkopayAdapter, FakeHttp]:
    http = FakeHttp()
    return JkopayAdapter(http, today=today), http  # type: ignore[arg-type]


def test_discover_counts_titles_and_urls() -> None:
    a, _ = adapter()
    stubs = a.discover()
    assert len(stubs) == 36
    keys = [s.key for s in stubs]
    assert len(keys) == len(set(keys)), "key 不得重複"
    by_key = {s.key: s for s in stubs}

    # 標題取卡片 mainTitle，不是內文
    assert by_key["campaign/taobao"].title == "街口X淘寶 11.11 火熱開跑"
    assert by_key["campaign/taobao"].period_start == date(2026, 11, 1)
    assert by_key["campaign/taobao"].period_end == date(2026, 11, 13)
    assert by_key["campaign/taobao"].summary == "天天免手續費，滿千再折百"
    # 單日、跨年檔期
    assert by_key["campaign/prosperityplaza"].period_end == date(2026, 12, 27)

    # 同一頁多張卡只列一筆：雙 11 三個檔期、Trip.com 兩檔
    assert sum(1 for s in stubs if s.url.endswith("/campaign/jko1111")) == 1
    assert "週二" not in by_key["campaign/tripcom"].title
    assert "Trip.com 週二品牌日" in by_key["campaign/tripcom"].summary

    # 街口自己的頁面一律 zh-TW 網址；其他一律是列表頁（白名單內）
    for stub in stubs:
        assert (
            stub.url == LIST_URL
            if stub.key.startswith("ext:")
            else stub.url.startswith("https://mkt.jkopay.com/zh-TW/")
        )
    assert by_key["campaign/mcdonalds"].url == "https://mkt.jkopay.com/zh-TW/campaign/mcdonalds"


def test_external_cards_and_tracking_params_removed() -> None:
    a, _ = adapter()
    stubs = a.discover()
    external = [s for s in stubs if s.key.startswith("ext:")]
    assert len(external) == 10
    seven = next(s for s in external if "7-11.com.tw" in s.key)
    assert seven.key == "ext:https://www.7-11.com.tw/special/article_new.aspx?item=Event_E038"
    assert "fbclid" not in seven.key
    with pytest.raises(ExternalTerms) as caught:
        a.terms(seven)
    assert caught.value.target == "https://www.7-11.com.tw/special/article_new.aspx?item=Event_E038"
    assert external_target("https://x.tw/a?b=1&fbclid=zz#f") == "https://x.tw/a?b=1"


def test_hidden_pages_use_html_title_and_skip_non_deals() -> None:
    a, http = adapter()
    stubs = {s.key: s for s in a.discover()}
    lunch = stubs["campaign/jkolunch"]
    assert lunch.title == "2026/10/1-12/31 週三午餐主題日，最高享30%"
    assert (lunch.period_start, lunch.period_end) == (date(2026, 10, 1), date(2026, 12, 31))
    assert stubs["campaign/jkomemberday"].period_end is None, "標題沒有期間就留空"
    assert "campaign/finance" not in stubs, "銀行優惠合集頁不是單一活動"
    assert not any(u.endswith("/newevent") and u != LIST_URL for u in http.calls)
    assert not any("newevent2026-11" in u for u in http.calls)


def test_expired_not_listed_and_404_skipped() -> None:
    a, _ = adapter(date(2026, 11, 14))
    keys = {s.key for s in a.discover()}
    assert "campaign/taobao" not in keys, "迄日 11/13 已過"
    assert "campaign/jko1111" not in keys
    assert "campaign/mcdonalds" in keys


def test_hidden_slug_404_is_skipped_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(HIDDEN, "campaign/jkolunch")
    a, _ = adapter()
    keys = {s.key for s in a.discover()}
    assert "campaign/jkolunch" not in keys
    assert any("jkolunch" in item for item in a.skipped)
    assert len(keys) == 35


def test_parse_period_formats() -> None:
    assert parse_period("2026/11/4 - 11/12") == (date(2026, 11, 4), date(2026, 11, 12))
    assert parse_period("2026/11/11") == (date(2026, 11, 11), date(2026, 11, 11))
    assert parse_period("2026/11/13- 11/30") == (date(2026, 11, 13), date(2026, 11, 30))
    assert parse_period("2026/12/20 - 1/5") == (date(2026, 12, 20), date(2027, 1, 5))
    assert parse_period("2026/10/6 - 2027/3/31")[1] == date(2027, 3, 31)
    assert parse_period("每週三") == (None, None)


def test_parse_cards_reads_structured_fields() -> None:
    cards = parse_cards(fixture("jkopay_list.html"))
    assert len(cards) == 26
    assert all(c["title"] and c["url"] for c in cards)


def test_terms_from_rsc_detail() -> None:
    a, _ = adapter()
    stub = Stub(
        provider_id="jkopay",
        key="campaign/jkopayebill",
        title="地價稅開徵囉！",
        url="https://mkt.jkopay.com/zh-TW/campaign/jkopayebill",
    )
    terms = a.terms(stub)
    assert 3000 < len(terms.text) < 8000
    for needle in ("每月限量 55,000 名", "先領先贏", "手動領取", "華南銀行", "單筆最高上限$30"):
        assert needle in terms.text
    # 頁尾、導覽、頁內錨點不混入
    for noise in (
        "版權所有",
        "客服專線",
        "立即下載街口支付",
        "繳費月曆\n繳費提醒教學\n街利存新戶3%",
    ):
        assert noise not in terms.text
    assert terms.title == "2026/10/6 - 12/31 街口支付繳費享最高 12% 回饋"


def test_detail_text_resolves_long_text_rows() -> None:
    text = detail_text(fixture("jkopay_detail.html"))
    # 「繳費付款方式」表格是以 $17 引用的長文字列，必須還原成內容
    assert "台電、中華電信、台灣大電信費" in text
    assert "$17" not in text
    charity = detail_text(fixture("jkopay_detail_charity.html"))
    assert "華南銀行" in charity and "喜憨兒基金會" in charity
