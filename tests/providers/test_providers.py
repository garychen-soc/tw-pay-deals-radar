"""四大宗擷取器：以 2026-11-06 取得的官方回應為 fixture，不連網。

每家驗三件事：標題只取結構化欄位、使用者網址格式正確（曾經踩過 404 的格式
在這裡鎖死）、條款文字限縮在內容容器且含關鍵條款。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from payradar.models import Stub
from payradar.providers import available, load
from payradar.providers.base import ExternalTerms, NotActivity
from payradar.providers.easywallet import EasyWalletAdapter
from payradar.providers.ipassmoney import IpassMoneyAdapter, js_redirect
from payradar.providers.pxpayplus import PxPayPlusAdapter
from payradar.providers.taiwanpay import TaiwanPayAdapter
from payradar.transport import BlockedURL, Response, is_allowed

FIXTURES = Path(__file__).parents[1] / "fixtures"
TODAY = date(2026, 11, 6)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


class FakeHttp:
    """依 URL 回應 fixture；``route`` 回傳 None 代表 404 等級的失敗。"""

    def __init__(self, route: Callable[[str, Any], str]) -> None:
        self.route = route
        self.calls: list[tuple[str, Any]] = []

    def get(self, url: str, *, json_body: Any = None, **_: object) -> Response:
        self.calls.append((url, json_body))
        body = self.route(url, json_body)
        return Response(url, url, 200, body, "text/html", "")


def test_registry_loads_every_module() -> None:
    for provider_id in available():
        cls = load(provider_id)
        assert cls.id == provider_id
        assert cls.domains and cls.name


# ---- 台灣Pay ---------------------------------------------------------------


def test_taiwanpay_discover_uses_fisc_page_and_drops_expired() -> None:
    http = FakeHttp(lambda url, body: fixture("taiwanpay_list.json"))
    adapter = TaiwanPayAdapter(http, today=TODAY)  # type: ignore[arg-type]
    stubs = adapter.discover()

    assert http.calls[0][1] == {"body": {}}, "列表 body 必須正好是 {'body':{}}"
    listed = json.loads(fixture("taiwanpay_list.json"))["body"]["recommendedCampaigns"]
    live = [c for c in listed if c.get("systemSeq") and c["endDate"] >= TODAY.isoformat()]
    assert len(stubs) == len(live) >= 3, "過期與無序號的不列"
    assert all(
        s.url.startswith("https://www.taiwanpay.com.tw/fisc-tpay/news/event/") for s in stubs
    )
    assert stubs[0].title == "新社大安TWQR消費趣 10%回饋很給利"
    assert stubs[0].period_end == date(2027, 3, 31)
    assert stubs[0].category == "掃碼支付（TWQR）"
    assert any("無 systemSeq" in s for s in adapter.skipped)


def test_taiwanpay_terms_from_detail_api() -> None:
    http = FakeHttp(lambda url, body: fixture("taiwanpay_detail.json"))
    adapter = TaiwanPayAdapter(http, today=TODAY)  # type: ignore[arg-type]
    stub = Stub(
        provider_id="taiwanpay",
        key="01M25DH7MX5RKQT8TFRGSSZCBF",
        title="t",
        url="https://www.taiwanpay.com.tw/x",
    )
    terms = adapter.terms(stub)

    assert http.calls[0][1] == {"body": {"systemSeq": "01M25DH7MX5RKQT8TFRGSSZCBF"}}
    assert "【活動內容】" in terms.text and "最高回饋新台幣" in terms.text
    assert "【活動限制】" in terms.text
    assert "<p>" not in terms.text
    assert "「行動銀行」APP 之參與機構" not in terms.text, "參與機構清單（joinBank）不收"
    assert terms.period_end == date(2027, 3, 31)


def test_taiwanpay_terms_rejects_error_code() -> None:
    busy = json.dumps({"header": {"returnCode": "TF9999", "returnMsg": "系統忙碌中"}})
    adapter = TaiwanPayAdapter(FakeHttp(lambda u, b: busy), today=TODAY)  # type: ignore[arg-type]
    stub = Stub(provider_id="taiwanpay", key="X", title="t", url="https://www.taiwanpay.com.tw/x")
    with pytest.raises(ValueError, match="TF9999"):
        adapter.terms(stub)


def test_taiwanpay_group_page_is_not_activity() -> None:
    http = FakeHttp(lambda url, body: fixture("taiwanpay_group.json"))
    adapter = TaiwanPayAdapter(http, today=TODAY)  # type: ignore[arg-type]
    stub = Stub(provider_id="taiwanpay", key="G", title="t", url="https://www.taiwanpay.com.tw/x")
    with pytest.raises(NotActivity):
        adapter.terms(stub)


def test_taiwanpay_info_template_keeps_other_information() -> None:
    http = FakeHttp(lambda url, body: fixture("taiwanpay_info.json"))
    adapter = TaiwanPayAdapter(http, today=TODAY)  # type: ignore[arg-type]
    stub = Stub(provider_id="taiwanpay", key="I", title="t", url="https://www.taiwanpay.com.tw/x")
    assert "台北捷運乘車碼優惠活動" in adapter.terms(stub).text


# ---- 全支付 -----------------------------------------------------------------


def _px_route(valid: dict[int, str]) -> Callable[[str, Any], str]:
    def route(url: str, _: Any) -> str:
        event_id = int(url.rsplit("/", 1)[1])
        return valid.get(event_id) or json.dumps({"code": "2001"})

    return route


def test_pxpayplus_scans_ids_until_tail_and_builds_terms() -> None:
    detail = fixture("pxpayplus_37.json")
    # 官方 id 實測連續（2026-11：1–153），尾端連續 25 個查無就停
    http = FakeHttp(_px_route({3: detail, 20: detail}))
    adapter = PxPayPlusAdapter(http, today=TODAY)  # type: ignore[arg-type]
    stubs = adapter.discover()

    assert [s.key for s in stubs] == ["3", "20"]
    assert stubs[1].url == (
        "https://marketing.pxpayplus.com/pxplus_marketing_page/activity_content_page?EventId=20"
    )
    assert max(int(u.rsplit("/", 1)[1]) for u, _ in http.calls) == 20 + 25
    text = stubs[0].detail_text or ""
    assert text.startswith("活動期間：2026/04/15 00:00 ~ 2026/12/31 23:59")
    assert "【注意事項】" in text and "單筆不限金額享10%回饋" in text
    assert "<li" not in text and "&nbsp;" not in text


def test_pxpayplus_drops_expired() -> None:
    data = json.loads(fixture("pxpayplus_37.json"))
    data["data"]["activity_end_time"] = "2026/10/01 23:59"
    http = FakeHttp(_px_route({5: json.dumps(data)}))
    assert PxPayPlusAdapter(http, today=TODAY).discover() == []  # type: ignore[arg-type]


# ---- 一卡通 -----------------------------------------------------------------


def _ipass_route(url: str, _: Any) -> str:
    if "page=1" in url:
        return fixture("ipass_list.html")
    if "page=" in url:
        return fixture("ipass_list.html")  # 官網翻過頭會重複最後一頁：要能停
    if url.endswith("GMZTIMYKIEY7I64rUuoF"):
        return fixture("ipass_redirect.html")
    if "static01-ipass" in url:
        return "<html><body><h1>10%回饋</h1><div>每人每月回饋上限200點</div></body></html>"
    return fixture("ipass_detail.html")


def test_ipass_discover_cards() -> None:
    http = FakeHttp(_ipass_route)
    adapter = IpassMoneyAdapter(http, today=TODAY)  # type: ignore[arg-type]
    stubs = adapter.discover()

    assert len(stubs) >= 10
    assert len({s.key for s in stubs}) == len(stubs)
    assert len([u for u, _ in http.calls if "page=" in u]) == 2, "第二頁沒有新卡片就停"
    first = stubs[0]
    assert first.title == "【 TWQR 掃碼繳稅】用 iPASS MONEY APP 繳地價稅即贈 50 元數位券"
    assert (first.period_start, first.period_end) == (date(2026, 10, 27), date(2026, 12, 3))
    assert first.url.startswith("https://www.i-pass.com.tw/Preferential/Detail/")
    assert all("貸" not in s.title for s in stubs)


def test_ipass_terms_scoped_to_article() -> None:
    adapter = IpassMoneyAdapter(FakeHttp(_ipass_route), today=TODAY)  # type: ignore[arg-type]
    stub = Stub(
        provider_id="ipassmoney",
        key="k",
        title="t",
        url="https://www.i-pass.com.tw/Preferential/Detail/GI2TSNQHW7ZKA64RZbVj",
    )
    terms = adapter.terms(stub)
    assert "11月份回饋上限已於2026/11/3 17:00 額滿" in terms.text
    assert "function fbs_click" not in terms.text
    assert "追蹤一卡通社群平台" not in terms.text


def test_ipass_follows_own_cdn_redirect() -> None:
    assert js_redirect(fixture("ipass_redirect.html")) == (
        "https://static01-ipass.cdn.hinet.net/ipassweb/10_Percent_Cashback/index.html"
    )
    adapter = IpassMoneyAdapter(FakeHttp(_ipass_route), today=TODAY)  # type: ignore[arg-type]
    stub = Stub(
        provider_id="ipassmoney",
        key="k",
        title="t",
        url="https://www.i-pass.com.tw/Preferential/Detail/GMZTIMYKIEY7I64rUuoF",
    )
    assert "每人每月回饋上限200點" in adapter.terms(stub).text


def test_ipass_partner_redirect_is_external() -> None:
    page = "<script>var redirectUrl = 'https%3a%2f%2fi-pass.co%2f9q7msa';</script>"
    adapter = IpassMoneyAdapter(FakeHttp(lambda u, b: page), today=TODAY)  # type: ignore[arg-type]
    stub = Stub(
        provider_id="ipassmoney",
        key="k",
        title="t",
        url="https://www.i-pass.com.tw/Preferential/Detail/X",
    )
    with pytest.raises(ExternalTerms) as caught:
        adapter.terms(stub)
    assert caught.value.target == "https://i-pass.co/9q7msa"


def test_ipass_http_redirect_off_site_is_external() -> None:
    class Blocked:
        def get(self, url: str, **_: object) -> Response:
            raise BlockedURL(
                "官方頁導向不可信任的位址（非官方網域、非 https 或為裸 IP）：https://richart.tw/x"
            )

    adapter = IpassMoneyAdapter(Blocked(), today=TODAY)  # type: ignore[arg-type]
    stub = Stub(
        provider_id="ipassmoney",
        key="k",
        title="t",
        url="https://www.i-pass.com.tw/Preferential/Detail/X",
    )
    with pytest.raises(ExternalTerms) as caught:
        adapter.terms(stub)
    assert caught.value.target == "https://richart.tw/x"


def test_ipass_whitelist_is_exact_cdn_host() -> None:
    domains = IpassMoneyAdapter.domains
    assert is_allowed("https://static01-ipass.cdn.hinet.net/a", domains)
    assert not is_allowed("https://other.cdn.hinet.net/a", domains)


# ---- 悠遊付 -----------------------------------------------------------------


def test_easywallet_discover_cards() -> None:
    http = FakeHttp(lambda u, b: fixture("easywallet_list.html"))
    stubs = EasyWalletAdapter(http, today=TODAY).discover()  # type: ignore[arg-type]

    assert len(stubs) >= 50
    assert len({s.key for s in stubs}) == len(stubs)
    assert all(
        s.url.startswith("https://easywallet.easycard.com.tw/benefit/content?id=") for s in stubs
    )
    assert all(".php" not in s.url for s in stubs)
    assert all(s.period_end and s.period_end >= TODAY for s in stubs)
    assert all(s.title for s in stubs)


def test_easywallet_terms_scoped() -> None:
    http = FakeHttp(lambda u, b: fixture("easywallet_detail.html"))
    adapter = EasyWalletAdapter(http, today=TODAY)  # type: ignore[arg-type]
    stub = Stub(
        provider_id="easywallet",
        key="1",
        title="t",
        url="https://easywallet.easycard.com.tw/benefit/content?id=1788682579",
    )
    terms = adapter.terms(stub)
    assert terms.title == "7-ELEVEN 嗶悠遊卡結帳滿$500現折$50"
    assert "10月份限量回饋2萬份於10/29(四)18:00已額滿" in terms.text
    assert len(terms.text) < 3000


def test_taiwanpay_uses_browser_from_its_own_portal() -> None:
    """Citrix 只放行瀏覽器（2026-11-14）；API 必須從同網域的官方頁內呼叫。"""
    assert TaiwanPayAdapter.transport == "browser"
    assert TaiwanPayAdapter.portal_url.startswith("https://www.taiwanpay.com.tw/")
    assert TaiwanPayAdapter.user_agent is None, "不偽造 UA，只換成瀏覽器"
