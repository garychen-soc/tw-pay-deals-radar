"""icash Pay：單頁「會員專屬優惠」＋ 同目錄的 JSON 條款檔。

**列表** ``https://www.icashpay.com.tw/event/memberbenefits/`` 是靜態 HTML。``<section
id="member-exclusive">`` 底下每張 ``div.card`` 是一個合作通路優惠::

    <div class="card"><div class="card-img"><img alt="統一超商"></div>
      <div class="card-title">點數回饋</div>
      <div class="card-content">使用 icash Pay 消費享 0.5% OPENPOINT 回饋</div>
      <div class="card-btn" data-id="01">優惠詳情</div></div>

被下架的通路是用 HTML 註解掩起來（統一精工、星巴克，實測 2026-11-06），解析器看不到註解，
所以只會列出現役的 11 張卡。``h2`` 下方的 ``<p>2026/7/1~12/31</p>`` 是整區共用的活動期間，
套用到每張卡（單張卡自己的期限若有，寫在條款文字裡，由後段判定）。

**條款**：「優惠詳情」是前端用 ``data-id`` 去查 ``./memberbenefits-data.json?v=…``
（網址從頁面的 ``fetch("./…json…")`` 取得，帶版本參數所以不寫死）。每筆
``{id, title, date, content:[{type:"text", value}|{type:"image", src}]}``；``title`` 是
通路名（也是 modal 標題，取為 ``Stub.title``），``date`` 欄位其實放優惠類型（點數回饋、
折抵優惠券…，取為 ``category``）。條款文字 = 卡片文案＋JSON 的文字段落；``image`` 段落
無法讀取，以「（此處條款為圖片：URL）」標示，讓後段看得出這張卡的條款有一部分是圖片
（實測 BEING spa 的 modal13 有一張圖）。

每張卡沒有獨立網址，``Stub.url`` 都是列表頁、``key`` 是 ``data-id``（兩位數字，頁面維護者
指派；通路下架後編號不會重用，實測 06、07 缺號）。條款在 discover 時就組好放進
``Stub.detail_text``，所以不需再逐筆抓明細（總共 2 個請求）。

實測 2026-11-06：預設 UA HTTP 200、不需瀏覽器；11 筆；條款平均約 190 字（多數很短，
只是文案＋少量細則，不是長篇條款）。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import urljoin

from ..models import Stub, Terms
from ..transport import TransportError
from .base import Adapter, node_text, tree

_PAGE = "https://www.icashpay.com.tw/event/memberbenefits/"
_JSON = re.compile(r"""fetch\(\s*["']([^"']*memberbenefits-data\.json[^"']*)["']""")
_PERIOD = re.compile(
    r"(20\d{2})/(\d{1,2})/(\d{1,2})\s*[~～\-－]\s*(?:(20\d{2})/)?(\d{1,2})/(\d{1,2})"
)


def parse_period(text: str) -> tuple[date | None, date | None]:
    match = _PERIOD.search(text or "")
    if not match:
        return None, None
    try:
        start = date(int(match[1]), int(match[2]), int(match[3]))
        end = date(int(match[4] or match[1]), int(match[5]), int(match[6]))
    except ValueError:
        return None, None
    return start, end


def json_url(html: str, base_url: str) -> str:
    match = _JSON.search(html)
    return urljoin(base_url, match[1]) if match else ""


def parse_cards(html: str) -> list[dict[str, str]]:
    """現役卡片（註解掉的不會出現）→ [{id, name, kind, text, start, end}]。"""
    page = tree(html)
    section = page.css_first("section#member-exclusive")
    if section is None:
        return []
    start, end = parse_period(node_text(section.css_first("article.content_title-group")))
    cards = []
    for card in section.css("div.card"):
        button = card.css_first("[data-id]")
        img = card.css_first("div.card-img img")
        cards.append(
            {
                "id": (button.attributes.get("data-id") or "") if button else "",
                "name": (img.attributes.get("alt") or "").strip() if img else "",
                "kind": node_text(card.css_first("div.card-title")),
                "text": node_text(card.css_first("div.card-content")),
                "start": start.isoformat() if start else "",
                "end": end.isoformat() if end else "",
            }
        )
    return cards


def render_terms(entry: dict[str, Any], base_url: str) -> str:
    lines = []
    for item in entry.get("content") or []:
        if item.get("type") == "image":
            lines.append(f"（此處條款為圖片：{urljoin(base_url, str(item.get('src') or ''))}）")
        elif value := str(item.get("value") or "").strip():
            lines.append(value)
    return "\n".join(lines)


class IcashPayAdapter(Adapter):
    id = "icashpay"
    name = "icash Pay"
    domains = ["icashpay.com.tw"]
    entry_url = _PAGE
    portal_url = _PAGE

    def discover(self) -> list[Stub]:
        response = self.get(_PAGE)
        entries: dict[str, dict[str, Any]] = {}
        if source := json_url(response.text, response.final_url):
            try:
                data = self.get_json(source)
                entries = {str(e.get("id")): e for e in data if isinstance(e, dict)}
            except TransportError as exc:
                self.failures.append(f"條款 JSON 讀取失敗：{exc}")

        stubs: list[Stub] = []
        for card in parse_cards(response.text):
            entry = entries.get(card["id"], {})
            title = str(entry.get("title") or card["name"])
            body = [title, card["kind"], card["text"]]
            if entry:
                body.append(render_terms(entry, response.final_url))
            stub = self.stub(
                key=card["id"] or title,
                title=title,
                url=_PAGE,
                period_start=date.fromisoformat(card["start"]) if card["start"] else None,
                period_end=date.fromisoformat(card["end"]) if card["end"] else None,
                summary=card["text"],
                category=str(entry.get("date") or card["kind"]),
                detail_text="\n".join(part for part in body if part),
            )
            if stub:
                stubs.append(stub)
        return stubs

    def terms(self, stub: Stub) -> Terms:
        # discover 已把條款放進 detail_text，pipeline 不會呼叫這裡；保留給直接呼叫的人
        return Terms(text=stub.detail_text or "", title=stub.title)


ADAPTER = IcashPayAdapter
