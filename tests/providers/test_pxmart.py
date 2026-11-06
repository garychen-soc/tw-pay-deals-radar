"""全聯 PX Pay 擷取器：fixture 是 2026-11-06 官網 ``__NEXT_DATA__`` 裁掉圖片欄位的存檔。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest

from payradar.models import Stub
from payradar.providers.base import NotActivity
from payradar.providers.pxmart import PxMartAdapter
from payradar.transport import Response

FIXTURES = Path(__file__).parents[1] / "fixtures"
TODAY = date(2026, 11, 6)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> Response:
        self.calls.append(url)
        path = unquote(url).removeprefix("https://www.pxmart.com.tw")
        routes = {
            "/campaign/pxpay-card": "pxmart_pxpay_card_list.html",
            "/campaign/latest": "pxmart_latest_list.html",
        }
        if path in routes:
            name = routes[path]
        elif path.startswith("/campaign/pxpay-card/"):
            name = "pxmart_detail_pxpay_card.html"
        elif path.startswith("/campaign/latest/"):
            name = "pxmart_detail_latest.html"
        else:
            return Response(url, url, 404, "", "text/html", "")
        return Response(url, url, 200, fixture(name), "text/html", "")


def adapter(today: date = TODAY) -> PxMartAdapter:
    return PxMartAdapter(FakeHttp(), today=today)  # type: ignore[arg-type]


def test_discover_pxpay_card_and_pxpay_titles_only() -> None:
    stubs = adapter().discover()
    titles = [s.title for s in stubs]

    assert len(stubs) == 6
    assert len({s.key for s in stubs}) == len(stubs)
    assert "2026Q4週年慶加碼 PX Pay.全支付 週六日消費最高6%活動" in titles
    assert "202611 PX Pay 發票記帳加碼獎" in titles
    # latest 的非 PX Pay 項目不收
    assert not any("福利任務" in t or "開獎號碼" in t or "購物節" in t for t in titles)
    first = stubs[0]
    assert (first.period_start, first.period_end) == (date(2026, 10, 30), date(2026, 12, 3))
    assert first.category == "PX Pay"


def test_urls_are_title_slugs_not_ids() -> None:
    stubs = adapter().discover()
    for stub in stubs:
        kind, title = stub.url.removeprefix("https://www.pxmart.com.tw/campaign/").split("/", 1)
        assert kind in {"pxpay-card", "latest"}
        assert " ".join(unquote(title).split()) == stub.title  # 網址保留官方原始空白
        assert " " not in stub.url
    glued = next(s for s in stubs if "玉山日" in s.title)
    assert glued.url.endswith(
        "2026%20%20%E5%85%A8%E8%81%AF%20%E9%80%B1%E4%B8%89%20%E7%8E%89%E5%B1%B1%E6%97%A5"
    )


def test_expired_not_listed() -> None:
    stubs = adapter(date(2026, 12, 10)).discover()
    titles = [s.title for s in stubs]
    assert "2026Q4週年慶加碼 PX Pay.全支付 週六日消費最高6%活動" not in titles  # 12/03 結束
    assert any("玉山日" in t for t in titles)  # 到 12/31


def test_terms_from_model_not_page_chrome() -> None:
    a = adapter()
    stub = Stub(
        provider_id="pxmart",
        key="pxpay-card:97",
        title="t",
        url="https://www.pxmart.com.tw/campaign/pxpay-card/x",
    )
    terms = a.terms(stub)
    assert "本活動不含儲值交易" in terms.text
    assert "注意事項" in terms.text and "福利點將於全聯系統確認" in terms.text
    assert "<p" not in terms.text and "ql-align" not in terms.text
    assert "客服" not in terms.text and "門市搜尋" not in terms.text
    assert terms.period_end == date(2026, 12, 3)


def test_terms_latest_detail() -> None:
    stub = Stub(
        provider_id="pxmart",
        key="latest:157",
        title="t",
        url="https://www.pxmart.com.tw/campaign/latest/x",
    )
    text = adapter().terms(stub).text
    assert "現金抵用券100元" in text and "同意並接受全聯實業股份有限公司" in text


def test_terms_without_model_is_not_activity() -> None:
    class Empty(FakeHttp):
        def get(self, url: str, **_: Any) -> Response:
            body = (
                '<script id="__NEXT_DATA__" type="application/json">'
                '{"props":{"pageProps":{}}}</script>'
            )
            return Response(url, url, 200, body, "text/html", "")

    a = PxMartAdapter(Empty(), today=TODAY)  # type: ignore[arg-type]
    with pytest.raises(NotActivity):
        a.terms(Stub(provider_id="pxmart", key="k", title="t", url="https://www.pxmart.com.tw/x"))
