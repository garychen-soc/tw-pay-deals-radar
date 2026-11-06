"""OPEN 錢包（7-ELEVEN／統一集團 OPENPOINT）：首頁內嵌的活動資料 ``var ptc = {...}``。

入口 ``https://www.openpoint.com.tw/`` 會 302 到 ``/auth`` → ``/auth/`` →
``/cdn/index.html``（登入頁的外殼不是我們要的，最後落地的 ``/cdn/index.html`` 才是
「OPENPOINT優惠活動」）。該頁是靜態 HTML，活動資料直接內嵌在 ``<script>``：
``var ptc = {MainBannerList, SevenElevenSelectList, CollectPointList, ExchangeList,
DiscountList, CreateDateTime}``（沒有額外 API，前端用 lodash 篩掉過期的）。

* 活動分四個頁籤：``SevenElevenSelectList``（OPENPOINT推薦，``tp=seven_eleven``）、
  ``CollectPointList``（累積點數）、``ExchangeList``（兌換點數）、``DiscountList``
  （優惠）。``MainBannerList`` 是首頁輪播，內容是這四類的子集加上 App 停機公告，
  **不當來源**（只含停機公告的項目就不會被收）。
* 每筆：``Code``（穩定唯一）、``Title``、``DateRange``（``活動時間：2026/07/01-2026/12/31``）、
  ``Description``（富文字 HTML）、``URL``（外部連結，常是 icash／icash Pay 頁面）。
* 使用者網址：``/cdn/event.html?tp={頁籤}&code={Code}``（官網自己的「優惠活動」明細頁，
  由前端 JS 依 ``code`` 渲染 ``Description``）。
* 去重：同一 ``Code`` 會同時出現在「推薦」與分類頁籤，以分類頁籤優先、只留一筆。
* 不收：標題含「教學／公告／停機」的（影片教學、停機公告）；結束日 ≥ 2099 的是常態服務
  說明（「OPENPOINT點數折抵代收/售」），不是優惠。

**條款**：實測 2026-11-06 的 ``Description`` 全是「一張圖片＋連結到 icash／icash Pay 官網
活動頁」，沒有任何文字。此時拋 ``ExternalTerms``（條款在 icash 網站），活動照列。
有文字的 ``Description`` 就直接當條款。

實測 2026-11-06：四個頁籤共 6 筆不重複，過濾後 3 筆（全是 icash 系活動：uniopen 聯名卡
自動加值 10%、icash Pay 週日最好Pay、icash Pay 綁 uniopen 聯名卡）—— 與 ``icash Pay``
擷取器高度重疊，後段需去重；舊版同期 7 筆。
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from ..models import Stub, Terms
from .base import Adapter, ExternalTerms, clean_title, html_fragment_text

_INDEX = "https://www.openpoint.com.tw/cdn/index.html"
_DETAIL = "https://www.openpoint.com.tw/cdn/event.html?tp={tp}&code={code}"
# 去重時分類頁籤優先於「推薦」
_TABS = (
    ("CollectPointList", "collect_point", "累積點數"),
    ("ExchangeList", "exchange", "兌換點數"),
    ("DiscountList", "discount", "優惠"),
    ("SevenElevenSelectList", "seven_eleven", "OPENPOINT推薦"),
)
_RANGE = re.compile(r"(20\d{2})/(\d{1,2})/(\d{1,2})\s*[-~]\s*(20\d{2})/(\d{1,2})/(\d{1,2})")
_NOT_OFFER = re.compile(r"教學|公告|停機")
_FOREVER_YEAR = 2099
_HREF = re.compile(r'href="(https?://[^"]+)"')


def parse_ptc(html: str) -> dict[str, Any]:
    marker = html.find("var ptc =")
    if marker < 0:
        raise ValueError("頁面缺少 var ptc（官網改版？）")
    start = html.index("{", marker)
    data, _ = json.JSONDecoder().raw_decode(html[start:])
    if not isinstance(data, dict):
        raise ValueError("ptc 不是物件")
    return data


def parse_range(text: str) -> tuple[date | None, date | None]:
    match = _RANGE.search(text or "")
    if not match:
        return None, None
    try:
        return (
            date(int(match[1]), int(match[2]), int(match[3])),
            date(int(match[4]), int(match[5]), int(match[6])),
        )
    except ValueError:
        return None, None


class OpenPointAdapter(Adapter):
    id = "openpoint"
    name = "OPEN 錢包"
    domains = ["openpoint.com.tw"]
    entry_url = _INDEX
    portal_url = _INDEX

    def _items(self) -> list[tuple[dict[str, Any], str, str]]:
        ptc = parse_ptc(self.get(_INDEX).text)
        rows: list[tuple[dict[str, Any], str, str]] = []
        seen: set[str] = set()
        for field, tab, label in _TABS:
            for item in ptc.get(field) or []:
                code = str(item.get("Code") or "")
                if code and code not in seen:
                    seen.add(code)
                    rows.append((item, tab, label))
        return rows

    def discover(self) -> list[Stub]:
        stubs: list[Stub] = []
        for item, tab, label in self._items():
            title = clean_title(str(item.get("Title") or ""))
            start, end = parse_range(str(item.get("DateRange") or ""))
            if end and end.year >= _FOREVER_YEAR:
                self.skipped.append(f"常態服務說明（無結束日）：{title}")
                continue
            if _NOT_OFFER.search(title):
                self.skipped.append(f"非優惠（教學／公告）：{title}")
                continue
            stub = self.stub(
                key=str(item["Code"]),
                title=title,
                url=_DETAIL.format(tp=tab, code=item["Code"]),
                period_start=start,
                period_end=end,
                category=label,
            )
            if stub:
                stubs.append(stub)
        return stubs

    def terms(self, stub: Stub) -> Terms:
        for item, _, _ in self._items():
            if str(item.get("Code")) != stub.key:
                continue
            html = str(item.get("Description") or "")
            text = html_fragment_text(html)
            if text:
                return Terms(text=text, title=stub.title)
            links = _HREF.findall(html) or ([str(item["URL"])] if item.get("URL") else [])
            raise ExternalTerms(links[0] if links else "（無連結）")
        raise ValueError(f"活動已從官網資料移除：{stub.key}")


ADAPTER = OpenPointAdapter
