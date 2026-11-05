"""悠遊付：支付回饋檔期活動列表＋明細頁。

列表 ``easywallet.easycard.com.tw/benefit`` 的卡片::

    <a href="/benefit/content.php?id=NNN" class="slider-card">
      …<p class="title">標題</p><p class="date">2026-08-01 － 2026-11-30</p>

使用者頁面用無 ``.php`` 的 ``/benefit/content?id=NNN``。列表沒有額滿／上限資訊，
要進明細讀「每月送完為止」之類的條款。

易誤判：這個網域前面是 HiNetCDN，headless 瀏覽器會被 WAF 擋成 502；一般 HTTP
抓取正常。若 HTTP 也回 502，是官網本身故障（2026-11-06 凌晨實測首頁 200、
``/benefit`` 502，真實瀏覽器同樣 502），沿用上一輪資料即可。
"""

from __future__ import annotations

import re
from datetime import date

from ..models import Stub, Terms
from .base import Adapter, main_text, node_text, tree

_LIST = "https://easywallet.easycard.com.tw/benefit"
_PAGE = "https://easywallet.easycard.com.tw/benefit/content?id={}"
_ID = re.compile(r"content(?:\.php)?\?id=(\d+)")
_RANGE = re.compile(r"(20\d{2}-\d{2}-\d{2})\s*[－–—~\-]\s*(20\d{2}-\d{2}-\d{2})")
# 明細頁內容容器（標題、期間、活動內容、注意事項都在裡面；2026-11 實測）
_CONTENT = "div.container-outline"


def _iso(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_list(html: str) -> list[tuple[str, str, date | None, date | None]]:
    """列表 HTML → [(活動 id, 標題, 起日, 迄日)]，同 id 只取第一張卡。"""
    rows: dict[str, tuple[str, str, date | None, date | None]] = {}
    for card in tree(html).css("a[href*='benefit/content']"):
        match = _ID.search(card.attributes.get("href") or "")
        if not match or match[1] in rows:
            continue
        title = node_text(card.css_first("p.title"))
        start = end = None
        if found := _RANGE.search(node_text(card.css_first("p.date"))):
            start, end = _iso(found[1]), _iso(found[2])
        rows[match[1]] = (match[1], title, start, end)
    return list(rows.values())


class EasyWalletAdapter(Adapter):
    id = "easywallet"
    name = "悠遊付"
    domains = ["easycard.com.tw"]
    entry_url = _LIST
    portal_url = _LIST

    def discover(self) -> list[Stub]:
        response = self.get(_LIST)
        stubs: list[Stub] = []
        for activity_id, title, start, end in parse_list(response.text):
            stub = self.stub(
                key=activity_id,
                title=title,
                url=_PAGE.format(activity_id),
                period_start=start,
                period_end=end,
            )
            if stub:
                stubs.append(stub)
        return stubs

    def terms(self, stub: Stub) -> Terms:
        response = self.get(stub.url)
        page = tree(response.text)
        return Terms(
            text=main_text(response.text, _CONTENT),
            title=node_text(page.css_first(f"{_CONTENT} h1") or page.css_first("h1")),
        )


ADAPTER = EasyWalletAdapter
