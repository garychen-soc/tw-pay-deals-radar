"""v2 資料契約（擷取層）。

``Stub``
    支付服務官方列表或 API 上的一筆活動。標題只能來自結構化欄位（卡片名稱、
    API 的 title 欄位），不得取自內文第一行。
``Terms``
    明細頁／明細 API 的條款純文字，是 LLM 定位回饋條件、額滿、登錄時點的
    唯一輸入。
``SourceHealth``
    一個支付服務本輪的抓取狀態。

發布用的 ``Activity`` 與抽取結果在 Phase 2 加入。所有日期是台北日期；資料檔
不放「今天」的函數（是否進行中、是否即將截止、是否高回饋），由前端計算。
"""

from __future__ import annotations

from datetime import date
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
