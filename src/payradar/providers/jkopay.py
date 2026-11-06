"""街口支付：Next.js（App Router）頁面建構器網站，資料全在 RSC payload 裡。

**列表** ``https://mkt.jkopay.com/zh-TW/campaign/newevent``：HTML 的 ``<body>`` 只有
骨架（client-side rendering），真正的資料在 ``self.__next_f.push([1,"…"])`` 的
RSC 串流裡，是頁面建構器的 JSON。「最新活動」區塊的每張卡片長這樣::

    {"mainTitle": {"text": "街口Ｘ淘寶 11.11 火熱開跑"},
     "subtitle": {"text": "天天免手續費，滿千再折百"},
     "head": {"text": "2026/11/1 - 11/13"},
     "urlOpenType": {"value": "https://mkt.jkopay.com/zh-TW/campaign/taobao"}}

標題取 ``mainTitle.text``、期間取 ``head.text``（兩種格式：``2026/11/4 - 11/12`` 與單日
``2026/11/11``，迄日省略年份時沿用起日年份、月份倒退則進位一年）。``showTime`` 是前端
顯示窗口，不是活動期間，不用。

* 卡片連到街口自己的頁面（``mkt.jkopay.com/{campaign|event}/{slug}``）→ 活動頁。
  同一頁被多張卡片指到（雙 11 三天、Trip.com 兩檔）只列一筆，以第一張卡的標題與期間為準，
  其餘卡片標題併入 ``summary``。
* 卡片連到合作商家／Facebook／短網址 → 條款不在街口網站，``Stub.url`` 只能放街口列表頁
  （網域白名單），``key`` 是 ``ext:<合作方網址>``，``terms()`` 拋 ``ExternalTerms`` 帶出該網址
  （已去掉 ``fbclid`` 追蹤參數）。
* 列表頁還埋有未放進「最新活動」卡片的活動頁連結（週三午餐日、週五美妍日…，圖片按鈕
  沒有文字標題）。這些用 regex 從同一份 RSC 抓 slug，再取明細頁 ``<title>`` 當標題
  （多半是「2026/10/1-12/31 週三午餐主題日，最高享30%」，期間從標題前綴解析，
  標題原樣保留）。404 的 slug 不收。明細頁在 discover 時就抓過，``terms()`` 直接用快取。
* ``campaign/finance``（銀行優惠合集頁，一頁列二十幾家銀行活動）不是單一活動，
  ``discover`` 直接濾掉。

**明細頁**同樣是 RSC：``"data":{"schema":[…],"values":{"zh-TW":{id:{…}}},"page":{…}}``。
``schema`` 是元件樹（決定閱讀順序），``values`` 以元件 id 存內容；富文字欄位（``content``）
長的會被串流成獨立的 ``T<hex長度>,`` 文字區塊並以 ``"$17"`` 引用，``rows()`` 負責還原。
條款文字 = 依元件樹順序收集 ``content/text/title/subtitle/head`` 欄位並轉純文字，
略過捲動導覽、回頂端、浮動元件與頁尾（``立即下載街口支付``、客服資訊）。
活動頁的 ``/campaign/x`` 與 ``/event/x`` 兩種路徑都存在，兩者內容格式相同；沒帶
``zh-TW`` 的舊連結會被站方導向 zh-TW，這裡一律正規化成 zh-TW 網址。

網域白名單 ``jkopay.com`` 只涵蓋 ``mkt.jkopay.com``；``asset.jkopay.com`` 是 JS/CSS 靜態檔、
``img.jkos.com.tw`` 是圖片，擷取都用不到，不放。

實測 2026-11-06（預設 UA 即可，HTTP 200，無需瀏覽器）：卡片 26 張 → 去重後 23 筆活動頁／
外部連結，另有 13 個隱藏活動頁 slug（共 36 筆；明細平均約 4,000 字）。
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import Stub, Terms
from ..transport import TransportError
from .base import Adapter, ExternalTerms, NotActivity, clean_title, html_fragment_text

_BASE = "https://mkt.jkopay.com/zh-TW"
_LIST = f"{_BASE}/campaign/newevent"
_OWN = re.compile(
    r"^https://mkt\.jkopay\.com/(?:zh-TW/)?(campaign|event)/([A-Za-z0-9_\-]+)/?(?:[?#].*)?$"
)
_ANY_OWN = re.compile(r"https://mkt\.jkopay\.com/(?:zh-TW/)?(campaign|event)/([A-Za-z0-9_\-]+)")
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_PERIOD = re.compile(
    r"\s*(20\d{2})/(\d{1,2})/(\d{1,2})\s*(?:[-－–~～]\s*(?:(20\d{2})/)?(\d{1,2})/(\d{1,2}))?"
)
# 列表頁自己、以及非單一活動的合集頁
_NOT_DEALS = frozenset({"finance"})
_LIST_SLUG = re.compile(r"^newevent")
_TEXT_KEYS = frozenset({"content", "text", "title", "subtitle", "mainTitle", "head"})
# 不是活動內容的元件：頁內捲動導覽、回頂端、浮動按鈕、廣告版位
_CHROME_TYPES = frozenset({"ScrollSticky", "ToTop", "FixedBox", "AdSlot"})
_FOOTER_LINES = re.compile(
    r"^(?:立即下載街口支付|查看活動|8:00 - 23:00 客服專線.*|客服信箱.*|版權所有.*)$"
)
_TRACKING = frozenset({"fbclid", "gclid"})


# ---- RSC 解析 -----------------------------------------------------------------


def rsc_text(html: str) -> str:
    """把所有 ``self.__next_f.push`` 的字串片段串回完整 RSC 串流。"""
    parts = []
    for chunk in _PUSH.findall(html):
        try:
            parts.append(json.loads(f'"{chunk}"'))
        except json.JSONDecodeError:
            continue
    return "".join(parts)


_ROW_KEY = re.compile(rb"([0-9a-f]+):")
_TEXT_ROW = re.compile(rb"T([0-9a-f]+),")


def rsc_rows(stream: str) -> dict[str, str]:
    """RSC 串流 → {列編號: 內容}。``T<hex>,`` 列是依「位元組數」切的長文字，可含換行。"""
    raw = stream.encode("utf-8")
    pos = 0
    rows: dict[str, str] = {}
    while pos < len(raw):
        key = _ROW_KEY.match(raw, pos)
        if not key:
            newline = raw.find(b"\n", pos)
            pos = len(raw) if newline < 0 else newline + 1
            continue
        start = key.end()
        long_text = _TEXT_ROW.match(raw, start)
        if long_text:
            size = int(long_text.group(1), 16)
            begin = long_text.end()
            rows[key.group(1).decode()] = raw[begin : begin + size].decode("utf-8", "replace")
            pos = begin + size
        else:
            newline = raw.find(b"\n", start)
            end = len(raw) if newline < 0 else newline
            rows[key.group(1).decode()] = raw[start:end].decode("utf-8", "replace")
            pos = end + 1
    return rows


def page_data(html: str) -> tuple[dict[str, Any], dict[str, str]]:
    """明細頁 → (頁面建構器資料, RSC 長文字列)。找不到就拋 ValueError。"""
    stream = rsc_text(html)
    marker = '"data":{"schema":'
    at = stream.find(marker)
    if at < 0:
        raise ValueError("頁面沒有建構器資料")
    data, _ = json.JSONDecoder().raw_decode(stream[at + len('"data":') :])
    if not isinstance(data, dict):
        raise ValueError("頁面建構器資料格式異常")
    return data, rsc_rows(stream)


def _ids_in_order(nodes: list[dict[str, Any]], skip_chrome: bool = True) -> list[str]:
    out: list[str] = []
    for node in nodes:
        if skip_chrome and node.get("type") in _CHROME_TYPES:
            continue
        out.append(str(node.get("id")))
        out.extend(_ids_in_order(node.get("children") or [], skip_chrome))
    return out


def _gather(value: Any, rows: dict[str, str], out: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _TEXT_KEYS and isinstance(item, str):
                if item.startswith("$") and item[1:] in rows:
                    item = rows[item[1:]]
                out.append(html_fragment_text(item) if "<" in item else item)
            else:
                _gather(item, rows, out)
    elif isinstance(value, list):
        for item in value:
            _gather(item, rows, out)


def detail_text(html: str) -> str:
    data, rows = page_data(html)
    values = (data.get("values") or {}).get("zh-TW") or {}
    lines: list[str] = []
    for component_id in _ids_in_order(data.get("schema") or []):
        chunk: list[str] = []
        _gather(values.get(component_id) or {}, rows, chunk)
        for text in chunk:
            for line in text.splitlines():
                line = line.strip()
                if line and not _FOOTER_LINES.match(line):
                    lines.append(line)
    return "\n".join(lines)


# ---- 列表解析 -----------------------------------------------------------------


def parse_period(text: str) -> tuple[date | None, date | None]:
    """``2026/11/4 - 11/12`` / ``2026/11/11`` / ``2026/10/6 - 2027/3/31`` → (起, 迄)。"""
    match = _PERIOD.match(text or "")
    if not match:
        return None, None
    try:
        start = date(int(match[1]), int(match[2]), int(match[3]))
        if not match[5]:
            return start, start
        year = int(match[4]) if match[4] else start.year
        end = date(year, int(match[5]), int(match[6]))
        if not match[4] and end < start:
            end = date(year + 1, int(match[5]), int(match[6]))
    except ValueError:
        return None, None
    return start, end


def parse_cards(html: str) -> list[dict[str, str]]:
    """列表頁 RSC 裡所有帶 ``mainTitle`` 的卡片 → [{title, subtitle, period, url}]。"""
    stream = rsc_text(html)
    decoder = json.JSONDecoder()
    cards: list[dict[str, str]] = []
    for found in re.finditer(r'"items":\[', stream):
        try:
            items, _ = decoder.raw_decode(stream[found.end() - 1 :])
        except json.JSONDecodeError:
            continue
        if not (items and isinstance(items[0], dict) and "mainTitle" in items[0]):
            continue
        for item in items:
            cards.append(
                {
                    "title": str((item.get("mainTitle") or {}).get("text") or ""),
                    "subtitle": str((item.get("subtitle") or {}).get("text") or ""),
                    "period": str((item.get("head") or {}).get("text") or ""),
                    "url": str((item.get("urlOpenType") or {}).get("value") or ""),
                }
            )
    return cards


def hidden_pages(html: str) -> list[str]:
    """RSC 裡出現過的所有街口活動頁 → 正規化網址（去重、排除列表頁自己）。"""
    found: list[str] = []
    for match in _ANY_OWN.finditer(rsc_text(html)):
        url = own_url(match[1], match[2])
        if _LIST_SLUG.match(match[2]) or url in found:
            continue
        found.append(url)
    return found


def own_url(kind: str, slug: str) -> str:
    return f"{_BASE}/{kind}/{slug}"


def external_target(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k not in _TRACKING])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def title_of(html: str) -> str:
    match = _TITLE.search(html)
    return clean_title(html_fragment_text(match[1])) if match else ""


class JkopayAdapter(Adapter):
    id = "jkopay"
    name = "街口支付"
    domains = ["jkopay.com"]
    entry_url = _LIST
    portal_url = _LIST

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pages: dict[str, str] = {}

    def discover(self) -> list[Stub]:
        html = self.get(_LIST).text
        stubs: dict[str, Stub] = {}

        for card in parse_cards(html):
            target = card["url"].strip()
            own = _OWN.match(target)
            start, end = parse_period(card["period"])
            if own and own[2] in _NOT_DEALS:
                continue
            if own:
                url = own_url(own[1], own[2])
                key = f"{own[1]}/{own[2]}"
            elif target.startswith("https://") and "jkopay.com" not in target:
                url, key = _LIST, f"ext:{external_target(target)}"
            else:
                self.skipped.append(f"卡片沒有可用連結：{card['title']}")
                continue
            if key in stubs:
                # 同一頁的另一張卡（例如雙 11 三個檔期）：標題併入摘要，期間以第一張為準
                previous = stubs[key]
                extra = clean_title(card["title"])
                if extra and extra not in previous.summary and extra != previous.title:
                    joined = f"{previous.summary} / {extra}" if previous.summary else extra
                    stubs[key] = previous.model_copy(update={"summary": joined})
                continue
            stub = self.stub(
                key=key,
                title=card["title"],
                url=url,
                period_start=start,
                period_end=end,
                summary=clean_title(card["subtitle"]),
            )
            if stub:
                stubs[key] = stub

        known = {s.url for s in stubs.values()}
        # 已過期的卡片也算「列表上有」，不要再從隱藏連結撿回來
        expired = {
            own_url(m[1], m[2])
            for card in parse_cards(html)
            if (m := _OWN.match(card["url"].strip()))
        }
        for url in hidden_pages(html):
            slug = url.rsplit("/", 1)[-1]
            if url in known or url in expired or slug in _NOT_DEALS:
                continue
            try:
                page = self.get(url).text
            except TransportError as exc:
                self.skipped.append(f"隱藏活動頁無法開啟（{exc}）：{url}")
                continue
            self._pages[url] = page
            title = title_of(page)
            start, end = parse_period(title)
            stub = self.stub(
                key="/".join(url.split("/")[-2:]),
                title=title,
                url=url,
                period_start=start,
                period_end=end,
            )
            if stub:
                stubs[stub.key] = stub
        return list(stubs.values())

    def terms(self, stub: Stub) -> Terms:
        if stub.key.startswith("ext:"):
            raise ExternalTerms(stub.key[4:])
        html = self._pages.get(stub.url) or self.get(stub.url).text
        try:
            text = detail_text(html)
        except ValueError as exc:
            raise NotActivity(str(exc)) from exc
        return Terms(text=text, title=title_of(html))


ADAPTER = JkopayAdapter
