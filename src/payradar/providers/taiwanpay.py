"""台灣Pay：TF020109 列表 API ＋ TF020110 明細 API。

官網是 SPA、活動內容是一張 PNG，HTML 抓不到東西；後端 JSON API 可直接 POST::

    列表 POST .../taiwanpayfapi/TF02/TF020109   body {"body":{}}
         → body.recommendedCampaigns[]：systemSeq / title / startDate / endDate / paymentType
    明細 POST .../taiwanpayfapi/TF02/TF020110   body {"body":{"systemSeq": ...}}
         → body.campaignDetail：location / target / content / reminder / restrictions / notes

列表 body 必須正好是 ``{"body":{}}``（實測多帶 header 欄位反而回空）；明細 body
格式錯會回 ``TF9999 系統忙碌中``，不是真的忙碌。

官方額滿標記在列表 ``title``：「(活動額滿)」「每月額滿」。這是全台支付服務中
唯一能確定「已額滿」的資料源 —— 標題原樣保留，由後段以標題為證據判定。

明細有三種 ``templateType``：1＝一般活動（上列欄位）；3＝說明頁（只有
``otherInformation`` 與幾張介紹圖，條款多半在圖裡）；2＝活動合集（``campaignGroups``
列出其他活動，那些活動在列表上本來就各有一筆）→ 拋 ``NotActivity`` 丟棄。

2026-11-14 起官網前面加了 Citrix NetScaler 防護：httpx／curl 不論 UA 一律 403（連首頁），
真實瀏覽器同一 IP 正常。因此改用 ``transport = "browser"``：開 ``portal_url`` 活動列表頁後，
在頁面內以 fetch 呼叫上面兩支 API（經 Gary 2026-11-14 核准；不登入、不解驗證碼）。

使用者頁面＝``/fisc-tpay/news/event/{systemSeq}``（必含 ``fisc-``；API 前綴
``/tpay/`` 套到頁面會 404）。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..models import Stub, Terms
from .base import Adapter, NotActivity, html_fragment_text

_API = "https://www.taiwanpay.com.tw/tpay/v1.0.0/950/taiwanpayfapi/TF02/{}"
_PAGE = "https://www.taiwanpay.com.tw/fisc-tpay/news/event/{}"
_PAYMENT_TYPE = {"1": "掃碼支付（TWQR）", "3": "條碼支付"}
_SECTIONS = (
    ("location", "活動地點"),
    ("target", "活動對象"),
    ("content", "活動內容"),
    ("reminder", "活動提醒"),
    ("restrictions", "活動限制"),
    ("notes", "注意事項"),
)


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def detail_text(detail: dict[str, Any]) -> str:
    """明細 API 的富文字欄位 → 條款全文。``joinBank``（參與機構清單）很長且無關條件，略過。"""
    parts: list[str] = []
    if detail.get("startDate") or detail.get("endDate"):
        parts.append(f"活動期間：{detail.get('startDate') or ''} ~ {detail.get('endDate') or ''}")
    description = (detail.get("description") or "").strip()
    if description and description != (detail.get("title") or "").strip():
        parts.append(description)
    for key, label in _SECTIONS:
        if text := html_fragment_text(str(detail.get(key) or "")):
            parts.append(f"【{label}】\n{text}")
    if text := html_fragment_text(str(detail.get("otherInformation") or "")):
        parts.append(text)
    return "\n".join(parts)


class TaiwanPayAdapter(Adapter):
    id = "taiwanpay"
    name = "台灣Pay"
    domains = ["taiwanpay.com.tw"]
    # 2026-11-14 起官網前加了 Citrix NetScaler，非瀏覽器客戶端一律 403（任何 UA 皆同）；
    # 改由瀏覽器開 portal_url 後在頁內呼叫同一支 API（Gary 2026-11-14 核准）。
    transport = "browser"
    entry_url = _API.format("TF020109")
    portal_url = "https://www.taiwanpay.com.tw/fisc-tpay/news/event"

    def discover(self) -> list[Stub]:
        payload = self.get_json(_API.format("TF020109"), json_body={"body": {}})
        campaigns = (payload.get("body") or {}).get("recommendedCampaigns")
        if not isinstance(campaigns, list):
            raise ValueError(f"TF020109 回傳格式改變：{str(payload)[:200]}")
        stubs: list[Stub] = []
        for item in campaigns:
            seq = str(item.get("systemSeq") or "").strip()
            if not seq:
                self.skipped.append(f"無 systemSeq：{item.get('title')}")
                continue
            stub = self.stub(
                key=seq,
                title=str(item.get("title") or ""),
                url=_PAGE.format(seq),
                period_start=_day(item.get("startDate")),
                period_end=_day(item.get("endDate")),
                category=_PAYMENT_TYPE.get(str(item.get("paymentType")), ""),
            )
            if stub:
                stubs.append(stub)
        return stubs

    def terms(self, stub: Stub) -> Terms:
        payload = self.get_json(
            _API.format("TF020110"), json_body={"body": {"systemSeq": stub.key}}
        )
        header = payload.get("header") or {}
        if header.get("returnCode") != "TF0000":
            raise ValueError(f"TF020110 {header.get('returnCode')}：{header.get('returnMsg')}")
        detail = (payload.get("body") or {}).get("campaignDetail") or {}
        if str(detail.get("templateType")) == "2" or detail.get("campaignGroups"):
            raise NotActivity("活動合集頁：其中各活動在列表上已各自收錄")
        return Terms(
            text=detail_text(detail),
            title=str(detail.get("title") or ""),
            period_start=_day(detail.get("startDate")),
            period_end=_day(detail.get("endDate")),
        )


ADAPTER = TaiwanPayAdapter
