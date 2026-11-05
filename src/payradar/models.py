"""v2 資料契約（擷取層）。

``Stub``
    支付服務官方列表或 API 上的一筆活動。標題只能來自結構化欄位（卡片名稱、
    API 的 title 欄位），不得取自內文第一行。
``Terms``
    明細頁／明細 API 的條款純文字，是 LLM 定位回饋條件、額滿、登錄時點的
    唯一輸入。
``SourceHealth``
    一個支付服務本輪的抓取狀態。

``Activity``
    發布到網站的一筆活動。一個官方頁面＝一筆活動；同頁多個子活動放在 ``offers``。

所有日期是台北日期；資料檔
不放「今天」的函數（是否進行中、是否即將截止、是否高回饋），由前端計算。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Stub(Strict):
    """列表／API 上的一筆官方活動。"""

    provider_id: str
    key: str = Field(description="該服務內穩定且唯一的鍵：API 的活動 id，或正規化後的明細 URL")
    title: str = Field(description="官方列表卡片名稱或 API 標題欄位，原樣保留")
    url: str = Field(description="使用者可開啟的官方活動頁，https 且在該服務網域白名單內")
    period_start: date | None = None
    period_end: date | None = None
    summary: str = Field(default="", description="列表卡片上的一句話摘要，沒有就留空")
    category: str = ""
    detail_text: str | None = Field(
        default=None,
        description="API 已附條款全文時直接放這裡，pipeline 就不再抓明細",
    )


class Terms(Strict):
    """明細的條款文字。"""

    text: str = Field(description="主要內容區純文字，已排除導覽列、頁尾、相關活動連結")
    title: str = Field(default="", description="明細自己的標題，只用於與 Stub.title 交叉檢查")
    period_start: date | None = None
    period_end: date | None = None


SourceStatus = Literal["ok", "partial", "failed", "blocked"]


class SourceHealth(Strict):
    provider_id: str
    provider_name: str
    status: SourceStatus
    entry_url: str
    stubs: int = 0
    activities: int = 0
    detail_failures: int = 0
    message: str = ""
    carried: int = 0


# ---- 抽取結果 ---------------------------------------------------------------
# 每個值都只在「引句逐字出現在條款原文、數字在引句裡找得到」時才存在；
# 驗不過就留空並記入 review，不採用 LLM 的說法。

SlotKind = Literal["opens_at", "range", "deadline"]
SlotOrigin = Literal["explicit", "rule", "enumerated"]


class Evidence(Strict):
    quote: str = Field(description="逐字取自條款原文（或官方列表標題）")
    url: str


class Slot(Strict):
    """一個具體的登錄／領券時點（沿用刷卡登錄雷達的換算）。"""

    start: datetime | None = Field(description="開放時刻；deadline 型為 None")
    end: datetime | None = Field(description="截止時刻；None 表示官方未公告或額滿為止")
    kind: SlotKind
    origin: SlotOrigin
    precision: Literal["minute", "day"] = "minute"
    holiday_shifted: bool = False
    note: str = ""
    evidence: Evidence


Reregister = Literal["each_period", "once", "unknown"]
Order = Literal["register_before_spend", "retroactive_ok", "unknown"]
Registration = Literal["required", "not_required", "unknown"]


class SubActivity(Strict):
    """登錄規則的部分（欄位與刷卡登錄雷達相同，``extract.schedule`` 產生）。"""

    name: str = ""
    slots: list[Slot] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list, description="登錄規則原文，供 UI 顯示")
    reregister: Reregister = "unknown"
    reregister_quote: str = ""
    order: Order = "unknown"
    order_quote: str = ""
    quota: int | None = Field(default=None, description="名額／總量（數字），沒寫 None")
    quota_quote: str = ""
    review: list[str] = Field(default_factory=list)


RewardKind = Literal["percent", "cash", "points", "coupon", "discount", "gift", "other"]
RewardUnit = Literal["%", "元", "點", "筆", "次", "份", "other"]
Per = Literal["txn", "day", "week", "month", "campaign", "unknown"]


class Reward(Strict):
    kind: RewardKind
    value: float | None = Field(description="數值；只寫「回饋」沒寫多少時為 None")
    unit: RewardUnit
    quote: str


class Amount(Strict):
    """門檻或上限：金額／點數＋計算區間。"""

    amount: int
    unit: RewardUnit
    per: Per = "unknown"
    quote: str


class SoldOut(Strict):
    """官方「已經額滿」的聲明（不是「額滿為止」這類條件句）。

    ``scope_start``/``scope_end`` 是這句話涵蓋的期間（「10月份…已額滿」＝10/1–10/31）；
    兩者皆 None 表示整檔活動。前端用它判斷「現在」是否額滿 —— 上個月額滿不代表本月額滿。
    """

    scope_label: str = Field(default="", description="原文的範圍字樣，如「10月份」「早鳥」")
    scope_start: date | None = None
    scope_end: date | None = None
    source: Literal["terms", "title"] = "terms"
    evidence: Evidence


Tri = Literal["yes", "no", "unknown"]


class Offer(SubActivity):
    """頁面上的一個子活動：登錄規則＋回饋條件＋名額。"""

    rewards: list[Reward] = Field(default_factory=list)
    min_spend: Amount | None = None
    caps: list[Amount] = Field(default_factory=list, description="每人回饋上限")
    new_users_only: Tri = "unknown"
    new_users_quote: str = ""
    where: str = Field(default="", description="適用通路原文")
    how: str = Field(default="", description="付款方式／綁定條件原文")
    limited: Tri = Field(default="unknown", description="是否限量（「限量」「送完為止」）")
    limited_quote: str = ""
    sold_out: list[SoldOut] = Field(default_factory=list)


TermsSource = Literal["official", "external", "image", "none"]


class Activity(Strict):
    id: str
    provider_id: str
    provider_name: str
    title: str
    aliases: list[str] = Field(default_factory=list)
    url: str
    period_start: date | None = None
    period_end: date | None = None
    category: str = ""
    terms_source: TermsSource = Field(
        default="official",
        description="external＝條款在合作方網站；image＝官方頁沒有可讀文字",
    )
    external_url: str = ""
    is_offer: Tri = "unknown"
    registration: Registration = "unknown"
    registration_quote: str = ""
    offers: list[Offer] = Field(default_factory=list)
    title_sold_out: list[SoldOut] = Field(
        default_factory=list, description="官方列表標題上的額滿標記（台灣Pay「(活動額滿)」）"
    )
    portal_url: str = ""
    review: list[str] = Field(default_factory=list)
    content_hash: str = ""
    observed_at: datetime
    stale: bool = Field(default=False, description="本輪來源失敗，沿用上一輪資料")
