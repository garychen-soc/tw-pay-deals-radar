"""橘子支付 GAMA PAY：官網自己前端呼叫的 JSON API（不需瀏覽器）。

``https://www.gamapay.com.tw/news_list.html`` 的內容是 JS 渲染（HTML 只有「資料載入中」），
但前端只是 ``fetch('/api/NewsList')``，一般 HTTP 直接取得：

* 列表 ``GET /api/NewsList`` → ``Data.News[]``：``Id``、``Type``、``PublishTime``
  （``2026/11/04``）、``Title``。``Type`` 是官網分類：1＝公告、2＝系統（維護通知）、
  3＝活動。**只收 ``Type == 3``**（實測 2026-11-06：370 筆中 94 筆活動，其餘是維護與
  公告）。
* 明細 ``GET /api/News/{Id}`` → ``Data.Title`` / ``Data.Content``（富文字 HTML，常夾
  base64 圖片）。使用者會點的網址是 ``/news.html?id={Id}``（與 API 端點不同）。

**踩到的坑**

* API 沒有活動期間欄位；「活動」分類裡混著歷史貼文（2025 年的全部也在），過期判斷
  只能看內文。做法：只處理最近 ``_RECENT_DAYS`` 天內發布的活動貼文（超過就當舊
  檔期不列，長期活動應會重新發文），再從 **標題＋內文** 抓明確的區間
  （``2026/11/4~11/9``、``即日起至 2026 年 12 月 31 日``），抓得到且已過期就不列；
  抓不到就 ``period_*`` 留空，交給後段 LLM。不推測。
* ``Type == 3`` 也含「中獎名單公告」，不是優惠，以標題「名單」濾掉。
* 內文多半是圖片＋一小段說明，條款常只在合作方網站（7-ELEVEN、台中市停車網）；
  那種就是文字很短，由後段標示。條款直接放 ``detail_text``（API 已含全文），
  pipeline 不用再抓明細。

實測 2026-11-06：``Type==3`` 94 筆，近 210 天 4 筆，未過期 4 筆。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from ..models import Stub, Terms
from .base import Adapter, clean_title, html_fragment_text

_BASE = "https://www.gamapay.com.tw"
_LIST_API = f"{_BASE}/api/NewsList"
_DETAIL_API = f"{_BASE}/api/News/{{}}"
_PAGE = f"{_BASE}/news.html?id={{}}"
_ACTIVITY_TYPE = 3
_RECENT_DAYS = 210
_NOT_OFFER = re.compile(r"名單")

_SEP = r"\s*[~～\-–—至到]\s*"
_FULL = r"(20\d{2})\s*[/年.]\s*(\d{1,2})\s*[/月.]\s*(\d{1,2})\s*日?"
_SHORT = r"(\d{1,2})\s*[/月]\s*(\d{1,2})\s*日?"
_RANGE_FULL = re.compile(_FULL + _SEP + _FULL)
_RANGE_SHORT = re.compile(_FULL + _SEP + _SHORT + r"(?!\s*[/年.]\s*\d)")
_UNTIL_FULL = re.compile(r"(?:即日起)?\s*至\s*" + _FULL)


def _date(y: str, m: str, d: str) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def parse_period(text: str) -> tuple[date | None, date | None]:
    """只認明確的區間寫法；認不得就回 (None, None)。多個區間取最晚的迄日。"""
    best: tuple[date | None, date | None] = (None, None)

    def offer(start: date | None, end: date | None) -> None:
        nonlocal best
        if end and (best[1] is None or end > best[1]):
            best = (start, end)

    for m in _RANGE_FULL.finditer(text):
        offer(_date(*m.group(1, 2, 3)), _date(*m.group(4, 5, 6)))
    for m in _RANGE_SHORT.finditer(text):
        start = _date(*m.group(1, 2, 3))
        end = _date(m.group(1), m.group(4), m.group(5))
        if start and end and end < start:  # 跨年：12/20~1/5
            end = _date(str(start.year + 1), m.group(4), m.group(5))
        offer(start, end)
    for m in _UNTIL_FULL.finditer(text):
        offer(None, _date(*m.group(1, 2, 3)))
    return best


def _publish_day(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%Y/%m/%d").date()
    except ValueError:
        return None


class GamaPayAdapter(Adapter):
    id = "gamapay"
    name = "橘子支付"
    domains = ["gamapay.com.tw"]
    entry_url = f"{_BASE}/news_list.html"
    portal_url = f"{_BASE}/news_list.html"

    def discover(self) -> list[Stub]:
        news = self.get_json(_LIST_API).get("Data", {}).get("News") or []
        cutoff = self.today - timedelta(days=_RECENT_DAYS)
        stubs: list[Stub] = []
        seen: set[str] = set()
        for item in news:
            if item.get("Type") != _ACTIVITY_TYPE:
                continue
            news_id = str(item.get("Id") or "")
            title = clean_title(str(item.get("Title") or ""))
            published = _publish_day(str(item.get("PublishTime") or ""))
            if not news_id or news_id in seen or published is None or published < cutoff:
                continue
            seen.add(news_id)
            if _NOT_OFFER.search(title):
                self.skipped.append(f"非優惠（名單公告）：{title}")
                continue
            try:
                body = self._body(news_id)
            except Exception as exc:  # 單筆失敗不拖垮整個來源
                self.failures.append(f"{news_id}: {exc}")
                continue
            start, end = parse_period(f"{title}\n{body}")
            stub = self.stub(
                key=news_id,
                title=title,
                url=_PAGE.format(news_id),
                period_start=start,
                period_end=end,
                detail_text=body,
            )
            if stub:
                stubs.append(stub)
        return stubs

    def terms(self, stub: Stub) -> Terms:
        return Terms(text=stub.detail_text or self._body(stub.key), title=stub.title)

    def _body(self, news_id: str) -> str:
        data = self.get_json(_DETAIL_API.format(news_id)).get("Data") or {}
        return html_fragment_text(str(data.get("Content") or ""))


ADAPTER = GamaPayAdapter
