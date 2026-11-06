"""歐付寶擷取器：2026-11-06 的官方回應為 fixture，不連網。

``opay_pages.json`` 的明細只保留 ``div.news_content_box`` 到下載 APP 區塊之前的片段
（其餘是共用導覽與頁尾）；``opay_detail.html`` 是完整頁面，用來驗證容器選擇器
確實把導覽與頁尾排除在外。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from payradar.models import Stub
from payradar.providers.base import ExternalTerms
from payradar.providers.opay import OpayAdapter, announcement_id, parse_list
from payradar.transport import BlockedURL, Response

FIXTURES = Path(__file__).parents[1] / "fixtures"
PAGES = json.loads((FIXTURES / "opay_pages.json").read_text("utf-8"))
TODAY = date(2026, 11, 6)


class FakeHttp:
    def __init__(self, *, blocked: str = "") -> None:
        self.calls: list[str] = []
        self.blocked = blocked

    def get(self, url: str, **_: Any) -> Response:
        self.calls.append(url)
        if url == "https://www.opay.tw/Banner/Event":
            body = (FIXTURES / "opay_list.html").read_text("utf-8")
            return Response(url, url, 200, body, "text/html", "")
        if url.startswith("https://p.opay.tw/"):
            if self.blocked and url.endswith(self.blocked):
                raise BlockedURL("官方頁導向不可信任的位址（非官方網域）：https://example.com/x")
            nid = PAGES["redirects"][url.rsplit("/", 1)[-1]]
            final = (
                f"https://www.opay.tw/Announcement/DetailAnnouncement?nID={nid}"
                "&utm_source=activity_website&utm_medium=banner"
            )
            return Response(url, final, 200, PAGES["details"][str(nid)], "text/html", "")
        raise AssertionError(f"未預期的請求 {url}")


def adapter(**kwargs: str) -> tuple[OpayAdapter, FakeHttp]:
    http = FakeHttp(**kwargs)
    return OpayAdapter(http, today=TODAY), http  # type: ignore[arg-type]


def test_parse_list_titles_from_h3() -> None:
    rows = parse_list((FIXTURES / "opay_list.html").read_text("utf-8"))
    assert len(rows) == 11
    assert rows[0] == (
        "2767",
        "中野安全帽頂上加碼，歐付寶掃TWQR付款成功享10%現金回饋",
        "https://p.opay.tw/iMNr6",
    )
    assert all(title and href.startswith("https://p.opay.tw/") for _, title, href in rows)


def test_discover_resolves_short_links_to_announcement_urls() -> None:
    a, _ = adapter()
    stubs = a.discover()
    assert len(stubs) == 11
    assert len({s.key for s in stubs}) == 11
    first = stubs[0]
    assert first.key == "4656"
    assert first.url == "https://www.opay.tw/Announcement/DetailAnnouncement?nID=4656"
    assert "utm_" not in first.url
    assert first.title == "中野安全帽頂上加碼，歐付寶掃TWQR付款成功享10%現金回饋"
    assert first.period_end is None, "列表沒有日期，不推測"
    assert any(s.title.startswith("2026/10/1~2026/12/31") for s in stubs), "標題原樣保留"
    assert a.failures == []


def test_terms_use_cached_page_and_keep_key_clauses() -> None:
    a, http = adapter()
    stubs = a.discover()
    calls = len(http.calls)
    terms = a.terms(stubs[0])
    assert len(http.calls) == calls, "discover 已抓過明細，terms 不重抓"
    for needle in (
        "單筆消費不限金額享10%現金回饋",
        "每一電子支付帳戶最高回饋新台幣500元",
        "額滿為止",
    ):
        assert needle in terms.text
    assert terms.title == "中野安全帽頂上加碼 歐付寶掃TWQR享10%"


def test_terms_exclude_navigation_and_footer_on_full_page() -> None:
    a, _ = adapter()
    stub = Stub(
        provider_id="opay",
        key="4656",
        title="x",
        url="https://www.opay.tw/Announcement/DetailAnnouncement?nID=4656",
    )
    a._pages[stub.url] = (FIXTURES / "opay_detail.html").read_text("utf-8")
    text = a.terms(stub).text
    assert "額滿為止" in text
    for noise in ("合作銀行", "使用者約定條款", "立即下載歐付寶 APP", "會員專區", "上一頁"):
        assert noise not in text


def test_short_link_leaving_opay_becomes_external_terms() -> None:
    a, _ = adapter(blocked="LjQWl")
    stubs = {s.key: s for s in a.discover()}
    assert len(stubs) == 11
    assert "banner-2757" in stubs
    external = stubs["banner-2757"]
    assert external.url == "https://p.opay.tw/LjQWl"
    with pytest.raises(ExternalTerms) as caught:
        a.terms(external)
    assert caught.value.target == "https://example.com/x"


def test_announcement_id() -> None:
    base = "https://www.opay.tw/Announcement/DetailAnnouncement"
    assert announcement_id(f"{base}?nID=4656&utm_source=a") == "4656"
    assert announcement_id("https://www.opay.tw/Banner/Event") == ""
    assert announcement_id(f"{base}?nID=abc") == ""
