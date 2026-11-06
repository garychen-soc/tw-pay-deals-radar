"""歐付寶 O'Pay：伺服器渲染的活動 banner 列表＋最新消息明細頁。

**列表** ``https://www.opay.tw/Banner/Event``：``#scw_01``「好康活動」區的每張 banner 是::

    <div class='ad-wrap'><div class="bn-title"><h3>標題</h3></div>
      <a target="_blank" href="https://p.opay.tw/iMNr6" banner="2767"><img …></a></div>

標題取 ``div.bn-title h3``（與圖片 alt 相同）。列表上**沒有日期**，``period_*`` 留空，
由後段從條款文字的「活動時間」判定；列表也不會自己下架舊活動（實測 2026-11-06 還掛著
2019 年的「動物園行動支付上線」banner），過期與否要靠條款日期，擷取層無從判斷。
「好友推薦活動」區（``#scw_02``）目前是空的，同一個選擇器照樣涵蓋。

**明細**：banner 連結是 ``p.opay.tw/<短碼>``（歐付寶自己的短網址，302），一律導向
``https://www.opay.tw/Announcement/DetailAnnouncement?nID=N&utm_*``。discover 時就跟著轉址
取得 nID（11 筆 11 次請求），``Stub.url`` 用去掉 utm 參數的 ``DetailAnnouncement?nID=N``，
``key`` 是 ``nID``；頁面快取到 ``terms()`` 免重抓。條款在 ``div.nd_article``，標題在
``p.nct_style``（與 banner 標題文字不同，只當交叉檢查）。若某個短碼導向非歐付寶網域，
``Fetcher`` 會拋 ``BlockedURL``：該筆 ``Stub.url`` 保留短網址、``key`` 用 ``banner-<編號>``，
``terms()`` 拋 ``ExternalTerms``。

``p.opay.tw`` 是 ``opay.tw`` 的子網域，白名單 ``opay.tw`` 已涵蓋。

實測 2026-11-06：預設 UA（帶專案識別字串）HTTP 200，**不需要瀏覽器**（過去 WebFetch 被擋是
工具端的問題，不是站方擋自動化）；11 筆；條款平均約 1,400 字，全為文字不是圖片。
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from ..models import Stub, Terms
from ..transport import BlockedURL, TransportError
from .base import Adapter, ExternalTerms, main_text, node_text, tree

_LIST = "https://www.opay.tw/Banner/Event"
_DETAIL = "https://www.opay.tw/Announcement/DetailAnnouncement?nID={}"
_CONTENT = "div.nd_article"
_NID = re.compile(r"/Announcement/DetailAnnouncement$", re.I)


def parse_list(html: str) -> list[tuple[str, str, str]]:
    """列表 HTML → [(banner 編號, 標題, 連結)]；沒有標題或連結的 banner 略過。"""
    rows: list[tuple[str, str, str]] = []
    for wrap in tree(html).css("div.ad-list div.ad-wrap"):
        link = wrap.css_first("a[banner]")
        if link is None:
            continue
        title = node_text(wrap.css_first("div.bn-title h3"))
        rows.append((link.attributes.get("banner") or "", title, link.attributes.get("href") or ""))
    return rows


def announcement_id(final_url: str) -> str:
    """最終網址 → nID；不是公告明細頁就回空字串。"""
    parts = urlsplit(final_url)
    if not _NID.search(parts.path):
        return ""
    values = parse_qs(parts.query).get("nID") or []
    return values[0] if values and values[0].isdigit() else ""


class OpayAdapter(Adapter):
    id = "opay"
    name = "歐付寶"
    domains = ["opay.tw"]
    entry_url = _LIST
    portal_url = _LIST

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._pages: dict[str, str] = {}
        self._external: dict[str, str] = {}

    def discover(self) -> list[Stub]:
        stubs: list[Stub] = []
        seen: set[str] = set()
        for banner, title, href in parse_list(self.get(_LIST).text):
            if not title:
                self.skipped.append(f"無標題：banner {banner}")
                continue
            key, url = f"banner-{banner}", href
            try:
                response = self.get(href)
            except BlockedURL as exc:
                self._external[key] = str(exc).rsplit("：", 1)[-1]
            except TransportError as exc:
                self.failures.append(f"{href}：{exc}")
                continue
            else:
                nid = announcement_id(response.final_url)
                if nid:
                    key, url = nid, _DETAIL.format(nid)
                    self._pages[url] = response.text
                else:
                    self._external[key] = response.final_url
            if key in seen:
                continue
            seen.add(key)
            stub = self.stub(key=key, title=title, url=url)
            if stub:
                stubs.append(stub)
        return stubs

    def terms(self, stub: Stub) -> Terms:
        if stub.key in self._external:
            raise ExternalTerms(self._external[stub.key])
        html = self._pages.get(stub.url) or self.get(stub.url).text
        page = tree(html)
        return Terms(
            text=main_text(html, _CONTENT),
            title=node_text(page.css_first("p.nct_style")),
        )


ADAPTER = OpayAdapter
