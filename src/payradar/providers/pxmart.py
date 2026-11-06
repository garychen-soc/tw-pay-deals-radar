"""全聯 PX Pay：官網（Next.js SSR）活動列表與明細，資料在 ``__NEXT_DATA__``。

舊入口 ``/campaign``（HTTP 404）已改成 ``/campaign/{type}``。實測 2026-11-06 的類型：

* ``pxpay-card``（「PX Pay 與信用卡」）：全收。5 筆，``label`` 是合作方
  （PX Pay、信用卡、玉山銀行、一卡通、悠遊卡）。
* ``latest``（「最新活動」）：只收標題含「PX Pay」或「全支付」的（例如
  「202611 PX Pay 發票記帳加碼獎」）。「福利任務」、「統一發票開獎號碼」、
  「家家購物節」是 App 任務／抽獎／節慶檔期，不是 PX Pay 支付優惠，不收。
* ``point``（印花積分換購）、``pre-order/list``（年菜預購）、``life-will``
  （重新導向）：商品活動，不收。

**列表** ``GET /campaign/{type}``：``<script id="__NEXT_DATA__">`` →
``props.pageProps.campaigns[]``，每筆 ``{id, attributes: {title, label, openDate,
closeDate}}``（日期 ``2026-10-30``）。標題只取 ``attributes.title``。列表沒有
「活動已結束」之外的狀態欄位，過期以 ``closeDate`` 判斷。

**明細（使用者網址）** ``/campaign/{type}/{title}``，``title`` 整段 percent-encode
（空格＝``%20``，連雙空格也要保留；以 ``id`` 組網址會 404）。明細的
``props.pageProps.model.attributes``：``description`` 與 ``contents[]``
（``content.paragraph`` 的 ``content`` 是富文字 HTML、``content.bookmark`` 是小標題）。
條款文字由這兩者組成，不抓整頁（整頁含導覽與「相關活動」）。

「全支付」（PX Pay＋全支付聯名）由 ``pxpayplus`` 擷取器處理；這裡的
「2026Q4週年慶加碼 PX Pay.全支付 週六日消費最高6%活動」是全聯官網自己的活動頁，
與全支付行銷站的活動是不同頁面，兩邊各收一次（由後段去重判斷）。

實測 2026-11-06：pxpay-card 5 筆未過期；latest 32 筆中 PX Pay 相關未過期 1 筆。
舊版同期 1 筆。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from urllib.parse import quote

from ..models import Stub, Terms
from .base import Adapter, NotActivity, html_fragment_text, tree

_BASE = "https://www.pxmart.com.tw"
_LIST = f"{_BASE}/campaign/{{}}"
_DETAIL = f"{_BASE}/campaign/{{}}/{{}}"
# 類型 → 標題必須含其中之一才收（None＝全收）
_TYPES: dict[str, tuple[str, ...] | None] = {
    "pxpay-card": None,
    "latest": ("PX Pay", "全支付"),
}


def next_data(html: str) -> dict[str, Any]:
    node = tree(html).css_first("script#__NEXT_DATA__")
    if node is None:
        raise ValueError("頁面缺少 __NEXT_DATA__（官網改版？）")
    data: dict[str, Any] = json.loads(node.text())
    return data


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class PxMartAdapter(Adapter):
    id = "pxmart"
    name = "全聯 PX Pay"
    domains = ["pxmart.com.tw"]
    entry_url = _LIST.format("pxpay-card")
    portal_url = _LIST.format("pxpay-card")

    def discover(self) -> list[Stub]:
        stubs: list[Stub] = []
        seen: set[str] = set()
        for kind, keywords in _TYPES.items():
            page = next_data(self.get(_LIST.format(kind)).text)
            for row in page["props"]["pageProps"].get("campaigns") or []:
                attrs = row.get("attributes") or {}
                title = str(attrs.get("title") or "")
                if keywords and not any(word in title for word in keywords):
                    continue
                key = f"{kind}:{row.get('id')}"
                if key in seen:
                    continue
                seen.add(key)
                stub = self.stub(
                    key=key,
                    title=title,
                    url=_DETAIL.format(kind, quote(title.strip(), safe="")),
                    period_start=_day(attrs.get("openDate")),
                    period_end=_day(attrs.get("closeDate")),
                    category=str(attrs.get("label") or ""),
                )
                if stub:
                    stubs.append(stub)
        return stubs

    def terms(self, stub: Stub) -> Terms:
        model = next_data(self.get(stub.url).text)["props"]["pageProps"].get("model")
        attrs = (model or {}).get("attributes")
        if not attrs:
            raise NotActivity(f"明細沒有活動內容：{stub.url}")
        parts = [html_fragment_text(str(attrs.get("description") or ""))]
        for block in attrs.get("contents") or []:
            head = str(block.get("title") or "").strip()
            if head:
                parts.append(head)
            parts.append(html_fragment_text(str(block.get("content") or "")))
        return Terms(
            text="\n".join(part for part in parts if part),
            title=str(attrs.get("title") or ""),
            period_start=_day(attrs.get("openDate")),
            period_end=_day(attrs.get("closeDate")),
        )


ADAPTER = PxMartAdapter
