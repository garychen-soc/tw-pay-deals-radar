"""支付服務擷取器介面。

每個服務一個模組，只回答兩件事：

``discover()``
    官方列表／API 上有哪些未過期活動 → ``list[Stub]``。
``terms(stub)``
    某筆活動的條款文字 → ``Terms``。

**硬規則：**

1. ``Stub.title`` 只能取自結構化欄位：列表卡片的標題元素、API 的 title
   欄位。找不到標題的卡片整筆丟棄並計入 ``skipped``，絕不拿內文第一行頂替。
2. 列表上的導覽、分類、App 下載、使用教學等非活動入口不列為 Stub。
3. ``Terms.text`` 必須限縮到主要內容容器（用 ``main_text(html, selector)``），
   導覽列、頁尾、側欄、「相關活動」連結不得混入。
4. ``Stub.url`` 是使用者會點的官方活動頁，不是 API 端點。兩者不同的服務
   （台灣Pay、全支付）在模組 docstring 寫清楚格式。
5. 只用 ``self.get()`` 取網頁 —— 它帶網域白名單、節流與重試；不登入、不解
   CAPTCHA、不點登錄／領券按鈕。預設 UA 帶專案識別字串；只有 ``user_agent``
   類別屬性明確覆寫的擷取器改用一般瀏覽器 UA（逐家經 Gary 核准，理由寫在
   該模組 docstring）。
6. 解析不到就留空（``None`` / ``""``），不推測。回饋、額滿、登錄時點都不在
   這層判斷，交給 LLM 定位＋程式驗證。
7. 已過期（``period_end`` < 今天）的活動在這層就不列，避免浪費明細抓取。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import date
from typing import Any, ClassVar, Literal
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser

from ..htmltext import scope_html, to_text
from ..models import Stub, Terms
from ..textnorm import normalize
from ..transport import Fetcher, Response

Transport = Literal["http", "browser"]
TAIPEI = ZoneInfo("Asia/Taipei")

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class ExternalTerms(Exception):
    """條款不在支付服務自己的網站上（明細頁直接轉到合作銀行、短網址）。

    處置是照樣發布這筆活動（標題與期間來自官方列表），但不抓、不抽取條款，
    前端標示「條款在合作方網站」並附官方明細網址。不放寬網域白名單去追
    第三方網站 —— 那等於把合作方的整個網站納入信任範圍。
    """

    def __init__(self, target: str) -> None:
        super().__init__(f"條款在合作方網站：{target}")
        self.target = target


class NotActivity(Exception):
    """明細顯示這筆其實不是活動（合集頁、功能介紹），pipeline 應丟棄並記入 skipped。

    只在「列表看不出來、要讀明細才知道」時使用；列表就能判斷的在 discover 過濾。
    """


def today_taipei() -> date:
    from datetime import datetime

    return datetime.now(TAIPEI).date()


class Adapter(ABC):
    id: ClassVar[str]
    name: ClassVar[str]
    domains: ClassVar[list[str]]
    entry_url: ClassVar[str]
    portal_url: ClassVar[str] = ""
    transport: ClassVar[Transport] = "http"
    user_agent: ClassVar[str | None] = None
    min_host_interval: ClassVar[float] = 0.5

    def __init__(self, http: Fetcher, *, today: date | None = None) -> None:
        self.http = http
        self.today = today or today_taipei()
        self.skipped: list[str] = []
        # discover 階段就逐筆抓明細的服務（全支付），單筆失敗記在這裡，
        # 由 pipeline 計入 detail_failures，不讓單筆失敗拖垮整個來源。
        self.failures: list[str] = []

    # ---- 子類實作 -------------------------------------------------------

    @abstractmethod
    def discover(self) -> list[Stub]: ...

    @abstractmethod
    def terms(self, stub: Stub) -> Terms: ...

    # ---- 共用工具 -------------------------------------------------------

    def get(
        self,
        url: str,
        *,
        data: dict[str, str] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        return self.http.get(
            url, data=data, json_body=json_body, headers=headers, conditional=False
        )

    def get_json(
        self,
        url: str,
        *,
        data: dict[str, str] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self.get(url, data=data, json_body=json_body, headers=headers).json()

    def stub(self, *, key: str, title: str, url: str, **fields: Any) -> Stub | None:
        """建 Stub；標題不合格或已過期就記入 ``skipped`` 並回 None。"""
        clean = clean_title(title)
        if not clean:
            self.skipped.append(f"無標題：{url}")
            return None
        end = fields.get("period_end")
        if isinstance(end, date) and end < self.today:
            return None
        return Stub(provider_id=self.id, key=key, title=clean, url=url, **fields)


_SPACE = re.compile(r"\s+")


def clean_title(title: str) -> str:
    """收斂空白與不可見字元；全空就回空字串。不做任何內容改寫。"""
    return _SPACE.sub(" ", normalize(title or "")).strip()


def tree(html: str) -> LexborHTMLParser:
    return LexborHTMLParser(html)


def node_text(node: Any) -> str:
    if node is None:
        return ""
    return _SPACE.sub(" ", node.text(separator=" ", strip=True) or "").strip()


def main_text(html: str, selector: str = "", *, tab_label: str = "") -> str:
    """主要內容區純文字。``selector`` 指向該服務明細頁的內容容器。"""
    return normalize(to_text(scope_html(html, selector=selector, tab_label=tab_label)))


def html_fragment_text(fragment: str) -> str:
    """API 回傳的 HTML 片段（富文字欄位）→ 純文字。"""
    if not fragment:
        return ""
    return normalize(to_text(scope_html(f"<div>{fragment}</div>", selector="")))


def absolute(base: str, href: str) -> str:
    return urljoin(base, href.strip())


def canonical(url: str, *, keep_query: bool = True) -> str:
    """去掉 fragment；必要時去掉 query。"""
    parts = urlsplit(url.strip())
    query = parts.query if keep_query else ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))
