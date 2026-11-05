"""一卡通 iPASS MONEY：伺服器渲染的列表分頁＋明細頁。

列表 ``/Preferential?type=0&page=N`` 只收「iPASS MONEY」分頁（type=0）；實體卡
與儲值卡分頁不是行動支付，不收。每張卡片是 ``div.portfolio-item``：標題取
``h3 > a``，期間取兩個 ``span.labeldate``（「2026/10/27 (二)」）。翻到沒有新卡片
就停。

明細頁內容在 ``div.article-post``。有些明細頁是「優惠總覽」，一頁列出多個
銀行合作子活動（各有自己的活動日期與額滿公告，例如「11月份回饋上限已於
2026/11/3 17:00 額滿」）—— 照樣整頁交給後段，由 LLM 切子活動。

部分明細頁只是一段 JS 轉址（``var redirectUrl = '<urlencoded>'``），目的地是
一卡通自己的活動網頁 ``static01-ipass.cdn.hinet.net/ipassweb/...``（該主機列在
i-pass.com.tw 的 CSP 白名單內、活動圖也放在那裡）。明細抓取跟著轉址讀目的頁；
``Stub.url`` 仍用 i-pass 的明細網址（使用者點了會被同樣導過去）。白名單只放
這一個完整主機名，不放 ``cdn.hinet.net``（那是中華電信共用 CDN）。

另一類明細頁轉到合作銀行或短網址（``i-pass.co``、``activity.ubot.com.tw``、
``richart.tw``…，2026-11 實測 42 筆中 12 筆）：條款在合作方網站，拋
``ExternalTerms``，活動照列但不抽取。列表上的信貸／貸款廣告不是支付優惠，不收。
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import unquote

from ..models import Stub, Terms
from ..transport import BlockedURL, is_allowed
from .base import Adapter, ExternalTerms, absolute, canonical, main_text, node_text, tree

_LIST = "https://www.i-pass.com.tw/Preferential?type=0&page={}"
_CONTENT = "div.article-post"
_DAY = re.compile(r"(20\d{2})/(\d{1,2})/(\d{1,2})")
_MAX_PAGES = 15
_JS_REDIRECT = re.compile(r"redirectUrl\s*=\s*'([^']+)'")
_NOT_PAYMENT = re.compile(r"信貸|貸款|免等貸|期貸|首期貸")


def _day(text: str) -> date | None:
    match = _DAY.search(text or "")
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def parse_list(html: str, base_url: str) -> list[tuple[str, str, date | None, date | None]]:
    """一頁列表 → [(明細 URL, 標題, 起日, 迄日)]。"""
    rows = []
    for card in tree(html).css("div.portfolio-item"):
        link = card.css_first("h3 a[href*='Preferential/Detail']")
        if link is None:
            continue
        dates = [_day(node_text(n)) for n in card.css("span.labeldate")]
        start = dates[0] if dates else None
        end = dates[1] if len(dates) > 1 else None
        url = canonical(absolute(base_url, link.attributes.get("href") or ""))
        rows.append((url, node_text(link), start, end))
    return rows


def js_redirect(html: str) -> str:
    """明細頁若只是 JS 轉址，回傳解碼後的目的網址；否則回空字串。"""
    if len(html) > 5000:
        return ""
    match = _JS_REDIRECT.search(html)
    return unquote(match[1]) if match else ""


class IpassMoneyAdapter(Adapter):
    id = "ipassmoney"
    name = "一卡通 iPASS MONEY"
    domains = ["i-pass.com.tw", "static01-ipass.cdn.hinet.net"]
    entry_url = _LIST.format(1)
    portal_url = "https://www.i-pass.com.tw/Preferential?type=0"

    def discover(self) -> list[Stub]:
        stubs: list[Stub] = []
        seen: set[str] = set()
        for page in range(1, _MAX_PAGES + 1):
            response = self.get(_LIST.format(page))
            rows = parse_list(response.text, response.final_url)
            fresh = [row for row in rows if row[0] not in seen]
            if not fresh:
                break
            for url, title, start, end in fresh:
                seen.add(url)
                if _NOT_PAYMENT.search(title):
                    self.skipped.append(f"非支付優惠（貸款廣告）：{title}")
                    continue
                stub = self.stub(key=url, title=title, url=url, period_start=start, period_end=end)
                if stub:
                    stubs.append(stub)
        return stubs

    def terms(self, stub: Stub) -> Terms:
        try:
            response = self.get(stub.url)
        except BlockedURL as exc:
            # HTTP 302 直接轉到合作方網站
            raise ExternalTerms(str(exc).rsplit("：", 1)[-1]) from exc
        if redirect := js_redirect(response.text):
            target = absolute(response.final_url, redirect)
            if not is_allowed(target, self.domains):
                raise ExternalTerms(target)
            # 一卡通自己的活動網頁：沒有 article-post，整頁交給 htmltext 去樣板
            landing = self.get(target)
            page = tree(landing.text)
            return Terms(text=main_text(landing.text), title=node_text(page.css_first("h1")))
        page = tree(response.text)
        return Terms(
            text=main_text(response.text, _CONTENT),
            title=node_text(page.css_first(f"{_CONTENT} h1") or page.css_first("h1")),
        )


ADAPTER = IpassMoneyAdapter
