"""全盈+PAY：官網「歷史活動」每月活動懶人包（伺服器渲染 HTML，條款全文在頁面內）。

**為什麼不用「當月活動」站** ``event2023.pluspay.com.tw`` 是 RyzoWEBA 頁面產生器做的
Angular SPA（HTTP 只有 3KB 空殼）。它的資料是單一 API
``/api/weba/v3/site/publish/event2023.pluspay.com.tw``，gzip 後 2.1MB、**解壓後 19.5MB**
（超過傳輸層 ``MAX_BYTES`` 8MB，且 ``Adapter.get`` 沒有放寬的參數），內容是整個
版面編輯器的元件樹（495 頁、每筆活動只是圖片＋兩段文字＋外部連結，條款是圖片、
銀行網站），標題／日期靠元件座標與 ``isShow`` 旗標推斷，不符合「標題只取結構化
欄位」。瀏覽器渲染只看得到預設頁籤（金融優惠），其餘頁籤要點擊。**結論：不採用。**

**採用的來源** ``www.pluspay.com.tw``（一定要帶 www）：

* 目錄 ``GET /csv/Promotion.csv``（UTF-8 帶 BOM；欄位 ``title_name`` 與 ``url``，
  ``url`` 形如 ``/202609Events``）。官網頁面是 JS 讀這個 CSV 畫列表。
* 每月一頁 ``GET /Campaign/Promotion/{YYYYMM}Events``（SSR）。頁面有兩份內容：
  ``.article_text`` 摘要（``h2`` 標題＋日期）與 ``.notice__content > div`` 的完整
  「活動注意事項」，每個活動一個 ``div``，內含 ``p.article-title``（標題，
  前綴「一、」、有時有【】）與 ``ul``（``活動時間：A - B``、活動說明、注意事項）。
  **標題取 ``p.article-title``、條款取該 ``div`` 全文**。
* 只掃最近 ``_MONTHS`` 個月（長期活動常跨數月，6 個月已涵蓋實測所有未過期項目），
  同標題＋同期間跨月重複者只留最新一份；已過期的不列。

**已知缺口（2026-11-06 實測）**：懶人包是月底才發布，目前最新是 ``202609Events``
（9/30 建立），**10 月起新開的活動（如 10/01–12/31 的各通路檔期）要等 10 月懶人包
發布才會出現**；11 月活動更晚。所以這個擷取器抓到的是「9 月以前開始、尚未結束」的
活動，漏掉「當月活動」站上最新的。補這個缺口需要共用功能：讓
``Adapter.get`` 支援 ``max_bytes`` 以讀 event2023 的 19.5MB 元件樹（見報告）。

**其他**

* 列表上的「信貸／貸款」（玉山 e 指信貸）不是支付優惠，濾掉。
* 日期寫法：``2026/09/12 20:00 - 2026/10/30``、``即日起 - 2026/12/31``（起日留空）、
  單日 ``2026/09/05``（起迄同日）、``(台灣時間)`` 後綴。
* 同一個月頁的多筆活動 ``Stub.url`` 都是該月懶人包網址（官網沒有個別活動的錨點）。
* ``Stub.key`` ＝ 標題＋起訖日（跨月穩定；「嘟粉專享隨買會員日」每月重複，但單日活動
  過了就被濾掉，同月不會撞鍵）。
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date

from ..models import Stub, Terms
from .base import Adapter, clean_title, html_fragment_text, node_text, tree

_BASE = "https://www.pluspay.com.tw"
_CSV = f"{_BASE}/csv/Promotion.csv"
_PAGE = f"{_BASE}/Campaign/Promotion/{{}}Events"
_MONTHS = 6
_URL_MONTH = re.compile(r"/(20\d{2})(\d{2})Events$")
_NOT_PAYMENT = re.compile(r"信貸|貸款")
_PREFIX = re.compile(r"^[一二三四五六七八九十]+、\s*")
_BRACKETS = re.compile(r"^【(.+)】$")
_DAY = re.compile(r"(20\d{2})/(\d{1,2})/(\d{1,2})")


def _day(match: re.Match[str] | None) -> date | None:
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def parse_period(line: str) -> tuple[date | None, date | None]:
    """「活動時間：2026/09/12 20:00 - 2026/10/30」→ (起, 迄)；「即日起」起日留空。"""
    days = list(_DAY.finditer(line))
    if not days:
        return None, None
    if len(days) == 1:
        only = _day(days[0])
        return (None, only) if "即日起" in line else (only, only)
    return _day(days[0]), _day(days[-1])


def clean_heading(raw: str) -> str:
    text = _PREFIX.sub("", clean_title(raw))
    match = _BRACKETS.match(text)
    return match[1].strip() if match else text


def catalog_months(csv_text: str) -> list[str]:
    """CSV → 最新到最舊的 ``YYYYMM`` 清單（只收月份懶人包，不收「2023常態活動」）。"""
    months: set[str] = set()
    for row in csv.DictReader(io.StringIO(csv_text.lstrip("﻿"))):
        match = _URL_MONTH.search((row.get("url") or "").strip())
        if match:
            months.add(match[1] + match[2])
    return sorted(months, reverse=True)


def parse_month(html: str) -> list[tuple[str, date | None, date | None, str]]:
    """一個月頁 → [(標題, 起, 迄, 條款全文)]；標題缺漏的區塊丟棄。"""
    rows = []
    for block in tree(html).css(".notice__content > div"):
        heading = block.css_first("p.article-title")
        title = clean_heading(node_text(heading))
        if not title:
            continue
        first = block.css_first("ul > li")
        start, end = parse_period(node_text(first))
        rows.append((title, start, end, html_fragment_text(block.html or "")))
    return rows


class PlusPayAdapter(Adapter):
    id = "pluspay"
    name = "全盈+PAY"
    domains = ["pluspay.com.tw"]
    entry_url = _CSV
    portal_url = f"{_BASE}/Campaign/Promotion"

    def discover(self) -> list[Stub]:
        months = catalog_months(self.get(_CSV).text)[:_MONTHS]
        if not months:
            raise ValueError("Promotion.csv 沒有任何月份懶人包（官網改版？）")
        stubs: dict[str, Stub] = {}
        for month in months:  # 新到舊：同鍵第一次出現的就是最新版
            url = _PAGE.format(month)
            try:
                html = self.get(url).text
            except Exception as exc:  # 單月失敗不拖垮整個來源
                self.failures.append(f"{month}: {exc}")
                continue
            for title, start, end, text in parse_month(html):
                if _NOT_PAYMENT.search(title):
                    self.skipped.append(f"非支付優惠（貸款廣告）：{title}")
                    continue
                key = f"{title}|{start or ''}|{end or ''}"
                if key in stubs:
                    continue
                stub = self.stub(
                    key=key,
                    title=title,
                    url=url,
                    period_start=start,
                    period_end=end,
                    detail_text=text,
                )
                if stub:
                    stubs[key] = stub
        return list(stubs.values())

    def terms(self, stub: Stub) -> Terms:
        # discover 已把條款放進 detail_text；理論上 pipeline 不會呼叫這裡。
        return Terms(text=stub.detail_text or "", title=stub.title)


ADAPTER = PlusPayAdapter
