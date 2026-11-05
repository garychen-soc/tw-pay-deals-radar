"""全支付：後端明細 API 逐 id 掃描。

官網是 CSR SPA，沒有可用的列表；活動明細有後端 API（伺服器端直接 GET）::

    GET https://service.pxpayplus.com/px-advertise/web/activity/detail/{id}
    code=="0000" 有效；"2001" 查無此 id；其餘視為錯誤

id 從 1 遞增，最後一個有效 id 之後連續 ``_TAIL`` 個查無就停。明細 API 一次
就帶齊條款（``info_content`` 的活動說明、子活動、提醒，以及 ``notice`` 注意事項），
所以 ``discover`` 直接把條款放進 ``Stub.detail_text``，不需要再抓明細。

使用者頁面＝``marketing.pxpayplus.com/pxplus_marketing_page/activity_content_page?EventId={id}``
（``marketing.`` 子網域；寫成 ``www.pxpayplus.com/...`` 會被導回官網首頁）。

**勿用** ``prod-s3.pxpayplus.com/MKT_Event/event{id}.json``：那是 2024 年的舊快取。
額滿公告只在 App 小鈴鐺，條款不會寫已額滿 —— 額滿狀態交給 LLM 讀條款時判為
「限量、額滿見 App」，不得推定為已額滿。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..models import Stub, Terms
from ..transport import TransportError
from .base import Adapter, html_fragment_text

_DETAIL = "https://service.pxpayplus.com/px-advertise/web/activity/detail/{}"
_PAGE = "https://marketing.pxpayplus.com/pxplus_marketing_page/activity_content_page?EventId={}"
_TAIL = 25
_MAX_ID = 2000


def _day(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y/%m/%d").date()
    except ValueError:
        return None


def detail_text(data: dict[str, Any]) -> str:
    """把明細 API 的富文字欄位組成條款全文（保留段落標題，供 LLM 判斷歸屬）。"""
    info = data.get("info_content") or {}
    parts: list[str] = []
    start, end = data.get("activity_start_time"), data.get("activity_end_time")
    if start or end:
        parts.append(f"活動期間：{start or ''} ~ {end or ''}")
    if text := html_fragment_text(str(info.get("content_title") or "")):
        parts.append(text)
    for block in info.get("content_block") or []:
        line = "：".join(x for x in (block.get("title"), block.get("content")) if x)
        if line:
            parts.append(line)
    for item in info.get("content_items") or []:
        if title := (item.get("title") or "").strip():
            parts.append(f"【{title}】")
        if text := html_fragment_text(str(item.get("content") or "")):
            parts.append(text)
    if text := html_fragment_text(str(info.get("remind") or "")):
        parts.append("【活動提醒】\n" + text)
    if text := html_fragment_text(str(data.get("notice") or "")):
        parts.append("【注意事項】\n" + text)
    for qa in info.get("question") or []:
        q, a = (qa.get("title") or "").strip(), (qa.get("content") or "").strip()
        if q or a:
            parts.append(f"Q：{q}\nA：{a}")
    return "\n".join(parts)


class PxPayPlusAdapter(Adapter):
    id = "pxpayplus"
    name = "全支付"
    domains = ["pxpayplus.com"]
    entry_url = "https://service.pxpayplus.com/px-advertise/web/activity/detail/1"
    portal_url = "https://www.pxpayplus.com/"
    min_host_interval = 0.15

    def discover(self) -> list[Stub]:
        stubs: list[Stub] = []
        last_found = 0
        errors = 0
        event_id = 0
        while event_id < _MAX_ID:
            event_id += 1
            if event_id - last_found > _TAIL and event_id > _TAIL:
                break
            try:
                payload = self.get_json(_DETAIL.format(event_id))
            except TransportError as exc:
                errors += 1
                self.failures.append(f"id {event_id}：{exc}")
                if errors >= 10 and not stubs:
                    raise
                continue
            code = str(payload.get("code"))
            if code == "2001":
                continue
            if code != "0000":
                self.failures.append(f"id {event_id}：API code {code}")
                continue
            last_found = event_id
            data = payload.get("data") or {}
            stub = self.stub(
                key=str(event_id),
                title=str(data.get("title") or ""),
                url=_PAGE.format(event_id),
                period_start=_day(data.get("activity_start_time")),
                period_end=_day(data.get("activity_end_time")),
                detail_text=detail_text(data),
            )
            if stub:
                stubs.append(stub)
        return stubs

    def terms(self, stub: Stub) -> Terms:
        payload = self.get_json(_DETAIL.format(stub.key))
        data = payload.get("data") or {}
        return Terms(
            text=detail_text(data),
            title=str(data.get("title") or ""),
            period_start=_day(data.get("activity_start_time")),
            period_end=_day(data.get("activity_end_time")),
        )


ADAPTER = PxPayPlusAdapter
