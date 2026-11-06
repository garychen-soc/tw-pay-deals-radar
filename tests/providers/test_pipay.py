"""Pi 拍錢包擷取器：2026-11-06 的官方回應為 fixture，不連網。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from payradar.models import Stub
from payradar.providers.pipay import PiPayAdapter, parse_list, parse_period
from payradar.transport import Response

FIXTURES = Path(__file__).parents[1] / "fixtures"
TODAY = date(2026, 11, 6)
BASE = "https://web.piapp.com.tw"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> Response:
        self.calls.append(url)
        routes = {
            f"{BASE}/events/": "pipay_list.html",
            f"{BASE}/events/page/2/": "pipay_list_p2.html",
            f"{BASE}/events-7-eleven-202611/": "pipay_detail.html",
            f"{BASE}/events-chubb2026/": "pipay_detail_chubb.html",
        }
        if url not in routes:
            raise AssertionError(f"未預期的請求 {url}")
        return Response(url, url, 200, fixture(routes[url]), "text/html", "")


def adapter() -> tuple[PiPayAdapter, FakeHttp]:
    http = FakeHttp()
    return PiPayAdapter(http, today=TODAY), http  # type: ignore[arg-type]


def test_discover_titles_urls_and_paging_stop() -> None:
    a, http = adapter()
    stubs = a.discover()
    assert http.calls == [f"{BASE}/events/", f"{BASE}/events/page/2/"], "第 2 頁全已過期就不再翻"
    assert [s.key for s in stubs] == [
        "2611p24",
        "261112bonus",
        "events-7-eleven-202611",
        "hucc-coop_202611",
        "events-chubb2026",
        "events-tmnewa2026",
        "allme-202605",
    ]
    assert len({s.url for s in stubs}) == len(stubs)
    # 網址是列表給的真實連結，不是自己拼的 events-{slug}
    assert stubs[0].url == f"{BASE}/2611p24/"
    assert stubs[2].title == "7-ELEVEN | 711 Day購物節🎊Pi 拍錢包消費滿$1,111立折$100"
    assert (stubs[2].period_start, stubs[2].period_end) == (date(2026, 11, 4), date(2026, 11, 9))
    assert (stubs[3].period_start, stubs[3].period_end) == (date(2026, 11, 16), date(2026, 12, 12))
    assert stubs[4].period_end is None, "摘要沒有日期就留空"
    assert (stubs[6].period_start, stubs[6].period_end) == (None, date(2026, 12, 31))


def test_loans_are_filtered_and_noted() -> None:
    a, _ = adapter()
    titles = [s.title for s in a.discover()]
    assert not any("撥款" in t or "貸" in t for t in titles)
    assert len([n for n in a.skipped if "貸款導流" in n]) == 5


def test_expired_posts_not_listed() -> None:
    a, _ = adapter()
    keys = {s.key for s in a.discover()}
    assert "events-7-eleven-202610" not in keys
    assert "events-bonus-202607" not in keys


def test_parse_list_and_pagination_link() -> None:
    rows, older = parse_list(fixture("pipay_list.html"), f"{BASE}/events/")
    assert len(rows) == 12
    assert older == f"{BASE}/events/page/2/"
    rows2, older2 = parse_list(fixture("pipay_list_p2.html"), f"{BASE}/events/page/2/")
    assert len(rows2) == 12 and older2 == f"{BASE}/events/page/3/"


def test_parse_period_only_with_explicit_year() -> None:
    assert parse_period("2026/11/1-11/11 Pi 拍錢包1111") == (date(2026, 11, 1), date(2026, 11, 11))
    assert parse_period("2026/07/01-2026/12/31 小額貸款") == (date(2026, 7, 1), date(2026, 12, 31))
    assert parse_period("2026 / 9 / 12- 10 / 31 ⏰ PChome") == (
        date(2026, 9, 12),
        date(2026, 10, 31),
    )
    assert parse_period("2025/9/1–10/31 中秋") == (date(2025, 9, 1), date(2025, 10, 31))
    assert parse_period("2026/07/01~2026/08/31 eSim") == (date(2026, 7, 1), date(2026, 8, 31))
    assert parse_period("即日起至2026/12/31，Pi 拍錢包") == (None, date(2026, 12, 31))
    assert parse_period("即日起-11/30~王道銀行") == (None, None), "沒有年份不推測"
    assert parse_period("7/1-9/30期間，新申辦慢點付") == (None, None)
    assert parse_period("2026/12/20-1/5 跨年") == (date(2026, 12, 20), date(2027, 1, 5))


def test_terms_scoped_to_entry_content() -> None:
    a, _ = adapter()
    stub = Stub(
        provider_id="pipay",
        key="events-7-eleven-202611",
        title="x",
        url=f"{BASE}/events-7-eleven-202611/",
    )
    terms = a.terms(stub)
    for needle in (
        "2026/11/04 (三) 00:00",
        "限量前 1.5 萬筆訂單",
        "總預算 150 萬元",
        "活動額滿請依 7-ELEVEN 系統公告為主",
    ):
        assert needle in terms.text
    for noise in ("Copyright", "玉山 Pi 拍錢包信用卡", "最新公告", "立即下載"):
        assert noise not in terms.text
    assert terms.title == "7-ELEVEN | 711 Day購物節🎊Pi 拍錢包消費滿$1,111立折$100"
