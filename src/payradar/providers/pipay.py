"""Pi 拍錢包：WordPress（Divi）部落格式活動列表＋文章明細頁。

**列表** ``https://web.piapp.com.tw/events/``（分頁 ``/events/page/N/``，由新到舊、每頁 12 篇）。
每篇是 ``article.et_pb_post``::

    <h2 class="entry-title"><a href="https://web.piapp.com.tw/events-7-eleven-202611/">標題</a></h2>
    <div class="post-content"><p>2026/11/4-11/9，7-ELEVEN DAY購物節…</p></div>

標題取 ``h2.entry-title a``、網址取該 ``href``（**不要**自己拼 ``events-{slug}/``：實測 slug 不
一致，有 ``2611p24``、``261112bonus``、``allme-202605``、``7-eleven-202604``、
``event_autopass_2026-2``…，只用列表給的真實連結，所以不會產生不存在的頁面）。
列表沒有結構化的期間欄位，只有摘要 ``div.post-content p``；摘要常以「2026/11/1-11/11」開頭，
**只在年份明確時**解析（``2026/7/1-8/31``、``2026/07/01~2026/08/31``、
``2026 / 9 / 12- 10 / 31``、``2025/9/1–10/31``；迄日省略年份時沿用起日年份）。
「即日起-11/30」「7/1-9/30期間」這類沒有年份的不解析（不推測年份），期間留空。

**分頁與過期**：列表由新到舊，但按發文日排序而非檔期迄日。只有「整頁每一篇都解析得到
迄日、且全部早於今天」才停止往更舊的頁翻；只要有一篇期間未知或未過期就繼續，上限 6 頁。
實測 2026-11-06：第 1 頁 12 篇都未過期、第 2 頁 12 篇迄日全已過期 → 只抓 2 頁。
（可能漏掉：較舊的文章其檔期延續到未來、但同一頁其他文章都已過期。實務上檔期最長約半年，
且這類長期活動多半會被新文章取代，風險低。）

**非支付項目**：標題含「貸」或「撥款」的是小額貸款／信貸導流（JUJI、王道銀行、拍享貸、
將來銀行、循環貸，贈 P 幣但不是支付優惠），比照 iPASS 濾掉並記入 ``skipped``。
保險導流（安達產險、新安東京海上）有 P 幣回饋，照列，由後段判斷。

**明細**：內容在 ``#left-area div.entry-content``（章節標題「一、活動時間」「三、活動內容」
「【注意事項】」都是文字，不是圖片）；標題在 ``h1.entry-title``。頁尾（Copyright）與頁首導覽不在
容器內。

網域白名單 ``piapp.com.tw`` 涵蓋 ``web.piapp.com.tw``（活動站）；官方導覽列另有
``www.piapp.com.tw``（會員站）。

實測 2026-11-06：預設 UA HTTP 200、不需瀏覽器；第 1 頁 12 篇濾掉 5 篇貸款後 7 筆；
明細平均約 3,900 字（短的 1,500 字、長的 7,700 字）。舊版 10 筆是 10 月的檔期（加油、中秋／
國慶 7-ELEVEN、9-10 月通路），現已過期；現在比較少是檔期自然更替，不是擷取遺漏。
"""

from __future__ import annotations

import re
from datetime import date

from ..models import Stub, Terms
from .base import Adapter, absolute, canonical, main_text, node_text, tree

_LIST = "https://web.piapp.com.tw/events/"
_CONTENT = "#left-area div.entry-content"
_MAX_PAGES = 6
_NOT_PAYMENT = re.compile(r"貸|撥款")
_SEP = r"\s*[-–—~～－]\s*"
_FULL = re.compile(
    rf"(20\d{{2}})\s*/\s*(\d{{1,2}})\s*/\s*(\d{{1,2}}){_SEP}"
    r"(?:(20\d{2})\s*/\s*)?(\d{1,2})\s*/\s*(\d{1,2})"
)
_UNTIL = re.compile(r"即日起\s*(?:至|[-–—~～－])\s*(20\d{2})\s*/\s*(\d{1,2})\s*/\s*(\d{1,2})")


def parse_period(text: str) -> tuple[date | None, date | None]:
    """摘要文字 → (起, 迄)；年份不明確就回 (None, None)。"""
    text = text or ""
    try:
        if match := _FULL.search(text):
            start = date(int(match[1]), int(match[2]), int(match[3]))
            end = date(int(match[4] or match[1]), int(match[5]), int(match[6]))
            if not match[4] and end < start:
                end = date(start.year + 1, end.month, end.day)
            return start, end
        if match := _UNTIL.search(text):
            return None, date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        pass
    return None, None


def parse_list(html: str, base_url: str) -> tuple[list[tuple[str, str, str]], str]:
    """一頁列表 → ([(網址, 標題, 摘要)], 下一頁網址或空字串)。"""
    page = tree(html)
    rows = []
    for post in page.css("article.et_pb_post"):
        link = post.css_first("h2.entry-title a")
        if link is None:
            continue
        url = canonical(absolute(base_url, link.attributes.get("href") or ""))
        rows.append((url, node_text(link), node_text(post.css_first("div.post-content p"))))
    older = page.css_first("div.pagination div.alignleft a")
    nxt = absolute(base_url, older.attributes.get("href") or "") if older else ""
    return rows, nxt


class PiPayAdapter(Adapter):
    id = "pipay"
    name = "Pi 拍錢包"
    domains = ["piapp.com.tw"]
    entry_url = _LIST
    portal_url = _LIST

    def discover(self) -> list[Stub]:
        stubs: list[Stub] = []
        seen: set[str] = set()
        url = _LIST
        for _ in range(_MAX_PAGES):
            response = self.get(url)
            rows, older = parse_list(response.text, response.final_url)
            all_expired = bool(rows)
            for link, title, excerpt in rows:
                start, end = parse_period(excerpt)
                if end is None or end >= self.today:
                    all_expired = False
                if link in seen:
                    continue
                seen.add(link)
                if _NOT_PAYMENT.search(title):
                    self.skipped.append(f"非支付優惠（貸款導流）：{title}")
                    continue
                stub = self.stub(
                    key=link.rstrip("/").rsplit("/", 1)[-1],
                    title=title,
                    url=link,
                    period_start=start,
                    period_end=end,
                    summary=excerpt,
                )
                if stub:
                    stubs.append(stub)
            if all_expired or not older or older in {url, ""}:
                break
            url = older
        return stubs

    def terms(self, stub: Stub) -> Terms:
        response = self.get(stub.url)
        page = tree(response.text)
        return Terms(
            text=main_text(response.text, _CONTENT),
            title=node_text(page.css_first(f"{_CONTENT} h1.entry-title") or page.css_first("h1")),
        )


ADAPTER = PiPayAdapter
