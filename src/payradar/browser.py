"""Read public HTML with a normal headed Chromium, preserving transport safeguards."""

from __future__ import annotations

import hashlib
import re
import time
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Page,
    Playwright,
    sync_playwright,
)
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import Response as BrowserResponse
from playwright.sync_api import TimeoutError as BrowserTimeout

from .transport import (
    MAX_BYTES,
    MAX_REDIRECTS,
    RETRY_ATTEMPTS,
    RETRY_BACKOFF_SECONDS,
    RETRY_STATUS,
    AccessDenied,
    BlockedURL,
    Fetcher,
    FetchFailed,
    Response,
    is_allowed,
)

CHALLENGE = r"just a moment|請稍候|请稍候|checking your browser|驗證您是真人|verify you are human"


class BrowserFetcher(Fetcher):
    """A headed browser is used only for sources declaring transport='browser'.

    No CAPTCHA solving, account login or custom browser identity. Linux callers
    provide a display (the workflow uses Xvfb). Each source owns its own browser.
    """

    def __init__(self, *args: Any, warmup_url: str = "", **kwargs: Any) -> None:
        # JSON API 只能從同網域的官方頁內呼叫：先開這一頁（通常是該服務的活動列表頁）
        self.warmup_url = warmup_url
        super().__init__(*args, **kwargs)
        self._timeout = float(self._client.timeout.read or 25.0)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._session: CDPSession | None = None
        self._main_frame = ""
        self._blocked_url = ""
        self._last_navigation: BrowserResponse | None = None
        self._document_count = 0
        self._redirect_limit = False

    def _guard_navigation(self, event: dict[str, Any]) -> None:
        assert self._session is not None
        url = str(event["request"]["url"])
        request_id = str(event["requestId"])
        if event.get("frameId") == self._main_frame:
            self._document_count += 1
            if self._document_count > MAX_REDIRECTS + 1:
                self._redirect_limit = True
                self._session.send(
                    "Fetch.failRequest",
                    {
                        "requestId": request_id,
                        "errorReason": "BlockedByClient",
                    },
                )
                return
        if not is_allowed(url, self.domains):
            if event.get("frameId") == self._main_frame:
                self._blocked_url = url
            self._session.send(
                "Fetch.failRequest",
                {
                    "requestId": request_id,
                    "errorReason": "BlockedByClient",
                },
            )
        else:
            self._session.send("Fetch.continueRequest", {"requestId": request_id})

    def _on_response(self, response: BrowserResponse) -> None:
        assert self._page is not None
        if response.request.is_navigation_request() and response.frame == self._page.main_frame:
            self._last_navigation = response

    def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=False)
            self._context = self._browser.new_context(locale="zh-TW", service_workers="block")
            self._page = self._context.new_page()
            self._session = self._context.new_cdp_session(self._page)
            frame_tree = self._session.send("Page.getFrameTree")
            self._main_frame = str(frame_tree["frameTree"]["frame"]["id"])
            self._session.on("Fetch.requestPaused", self._guard_navigation)
            # CDP pauses each document request, including redirect hops, before sending it.
            self._session.send(
                "Fetch.enable",
                {
                    "patterns": [
                        {"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}
                    ],
                },
            )
            self._session.send("Network.setCacheDisabled", {"cacheDisabled": True})
            self._page.on("response", self._on_response)
        except BrowserError as exc:
            self.close()
            raise FetchFailed("瀏覽器啟動失敗；需安裝 Chromium，Linux 需透過 Xvfb 執行") from exc

    def _read(self, url: str, headers: dict[str, str], max_bytes: int) -> Response:
        assert self._page is not None and self._context is not None
        self._context.set_extra_http_headers(headers)
        self._blocked_url = ""
        self._last_navigation = None
        self._document_count = 0
        self._redirect_limit = False
        response = self._page.goto(url, wait_until="domcontentloaded", timeout=self._timeout * 1000)
        if re.search(CHALLENGE, self._page.title(), re.I):
            try:
                self._page.wait_for_function(
                    "pattern => !new RegExp(pattern, 'i').test(document.title)",
                    arg=CHALLENGE,
                    timeout=10000,
                )
            except BrowserTimeout as exc:
                raise AccessDenied(f"來源仍要求驗證，未讀取活動資料：{url}") from exc
        if self._blocked_url:
            raise BlockedURL(f"官方頁導向不可信任的位址：{self._blocked_url}")
        if self._redirect_limit:
            raise FetchFailed(f"redirect 次數過多：{url}")
        final_url = self._page.url
        if not is_allowed(final_url, self.domains):
            raise BlockedURL(f"官方頁導向不可信任的位址：{final_url}")
        response = self._last_navigation or response
        if response is None:
            raise FetchFailed(f"官方頁沒有 HTTP 回應：{url}")
        status = response.status
        if status in {401, 403, 429}:
            raise AccessDenied(f"來源拒絕存取：{final_url} HTTP {status}")
        if status == 304 or status >= 400:
            raise FetchFailed(f"抓取失敗：{final_url} HTTP {status}")
        # Preserve the server document consumed by the existing HTML parser.
        # Portal scripts can remove/rewrite content after load; DOM serialization
        # lost 19 HNCB offers even though all document requests succeeded.
        content = response.body()
        if len(content) > max_bytes:
            raise FetchFailed(f"回應超過 {max_bytes} bytes：{final_url}")
        content_type = response.all_headers().get("content-type", "text/html")
        charset = re.search(r"charset\s*=\s*[\"']?([^\s;\"']+)", content_type, re.I)
        encoding = charset.group(1) if charset else "utf-8"
        try:
            html = content.decode(encoding, errors="replace")
        except LookupError:
            html = content.decode("utf-8", errors="replace")
        return Response(
            requested_url=url,
            final_url=final_url,
            status_code=status,
            text=html,
            content_type=content_type.split(";")[0],
            content_hash=hashlib.sha256(content).hexdigest(),
        )

    def _post_json(self, url: str, payload: Any, max_bytes: int) -> Response:
        """在同網域的官方頁內以 fetch 送出唯讀的 JSON 查詢（官網前端自己呼叫的那支 API）。

        2026-11-14 起台灣Pay 前面加了 Citrix NetScaler：httpx／curl（任何 UA）連首頁都 403，
        真實瀏覽器同一 IP、同一支 API 回 200。這裡不偽造身分、不解驗證碼，只是讓請求從
        瀏覽器裡的官方頁發出（Gary 2026-11-14 核准）。
        """
        assert self._page is not None
        target = urlsplit(url)
        origin = f"{target.scheme}://{target.netloc}"
        if not self._page.url.startswith(origin + "/"):
            warmup = self.warmup_url if self.warmup_url.startswith(origin + "/") else origin + "/"
            self._read(warmup, {}, MAX_BYTES)
        result = self._page.evaluate(
            """async ({url, body}) => {
                const r = await fetch(url, {
                    method: "POST", credentials: "same-origin",
                    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
                });
                return {status: r.status, url: r.url, type: r.headers.get("content-type") || "",
                        text: await r.text()};
            }""",
            {"url": url, "body": payload},
        )
        final_url = str(result["url"])
        if not is_allowed(final_url, self.domains):
            raise BlockedURL(f"API 導向不可信任的位址：{final_url}")
        status = int(result["status"])
        if status in {401, 403, 429}:
            raise AccessDenied(f"來源拒絕存取：{final_url} HTTP {status}")
        if status >= 400:
            raise FetchFailed(f"抓取失敗：{final_url} HTTP {status}")
        text = str(result["text"])
        content = text.encode("utf-8")
        if len(content) > max_bytes:
            raise FetchFailed(f"回應超過 {max_bytes} bytes：{final_url}")
        return Response(
            requested_url=url,
            final_url=final_url,
            status_code=status,
            text=text,
            content_type=str(result["type"]).split(";")[0],
            content_hash=hashlib.sha256(content).hexdigest(),
        )

    def get(
        self,
        url: str,
        *,
        data: dict[str, str] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        conditional: bool = True,
        max_bytes: int = MAX_BYTES,
    ) -> Response:
        if not is_allowed(url, self.domains):
            raise BlockedURL(f"URL 未通過安全檢查：{url}")
        if data is not None:
            raise FetchFailed("瀏覽器來源不提交表單")
        self._ensure_browser()
        host = urlsplit(url).hostname or ""
        for attempt in range(RETRY_ATTEMPTS):
            if attempt:
                time.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
            self._throttle(host)
            try:
                if json_body is not None:
                    return self._post_json(url, json_body, max_bytes)
                return self._read(url, headers or {}, max_bytes)
            except BrowserError as exc:
                if self._blocked_url:
                    raise BlockedURL(f"官方頁導向不可信任的位址：{self._blocked_url}") from exc
                if self._redirect_limit:
                    raise FetchFailed(f"redirect 次數過多：{url}") from exc
                if attempt == RETRY_ATTEMPTS - 1:
                    reason = str(exc).splitlines()[0][:200]
                    message = f"瀏覽器抓取失敗：{url}（已重試 {RETRY_ATTEMPTS} 次；{reason}）"
                    raise FetchFailed(message) from exc
            except FetchFailed as exc:
                if (
                    isinstance(exc, AccessDenied)
                    or not any(f"HTTP {status}" in str(exc) for status in RETRY_STATUS)
                    or attempt == RETRY_ATTEMPTS - 1
                ):
                    raise
        raise FetchFailed(f"瀏覽器抓取失敗：{url}")

    def close(self) -> None:
        if self._browser is not None:
            with suppress(BrowserError):
                self._browser.close()
        if self._playwright is not None:
            with suppress(BrowserError):
                self._playwright.stop()
        self._page = None
        self._browser = None
        self._context = None
        self._session = None
        self._playwright = None
        super().close()
