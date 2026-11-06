"""PTT 社群情報（非官方，前端獨立一區、標明需查證）。

抓幾個看板首頁的文章標題／推文數／連結，留下與支付優惠相關的。這些是社群
來源：時效快、常有「額滿」第一手回報，但未經官方確認 —— 不進 state、不送
LLM、不影響任何活動的欄位，只在網站「社群」分頁原樣列出。

失敗不影響發布：抓不到就沿用上一份 ptt.json。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .providers.base import node_text, tree
from .transport import Fetcher, TransportError

BOARDS = ("lifeismoney", "MobilePay", "e-coupon", "CVS")
KEYWORDS = re.compile(
    r"pay|支付|回饋|優惠|折|券|額滿|街口|悠遊|全支付|icash|台灣Pay|錢包|點數|TWQR", re.I
)
OUT = Path(__file__).resolve().parents[2] / "web" / "public" / "data" / "ptt.json"
MAX_POSTS = 40


def parse_board(html: str, board: str) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    for entry in tree(html).css("div.r-ent"):
        link = entry.css_first("div.title a")
        if link is None:  # 「(本文已被刪除)」沒有連結
            continue
        title = node_text(link)
        if not title or not KEYWORDS.search(title):
            continue
        posts.append(
            {
                "board": board,
                "title": title,
                "url": "https://www.ptt.cc" + (link.attributes.get("href") or ""),
                "push": node_text(entry.css_first("div.nrec")),
                "date": node_text(entry.css_first("div.date")),
            }
        )
    return posts


def _push_score(push: str) -> int:
    if push == "爆":
        return 100
    return int(push) if push.isdigit() else 0


def collect() -> dict[str, Any]:
    posts: list[dict[str, Any]] = []
    failures: list[str] = []
    with Fetcher(["ptt.cc"]) as http:
        for board in BOARDS:
            try:
                html = http.get(
                    f"https://www.ptt.cc/bbs/{board}/index.html", conditional=False
                ).text
            except TransportError as exc:
                failures.append(f"{board}：{exc}")
                continue
            posts += parse_board(html, board)
    posts.sort(key=lambda p: -_push_score(p["push"]))
    return {
        "fetched_at": datetime.now(ZoneInfo("Asia/Taipei")).replace(microsecond=0).isoformat(),
        "boards": list(BOARDS),
        "failures": failures,
        "posts": posts[:MAX_POSTS],
    }


def write(out: Path = OUT) -> dict[str, Any]:
    """全部看板都失敗時不覆寫上一份。"""
    payload = collect()
    if not payload["posts"] and len(payload["failures"]) == len(BOARDS) and out.exists():
        return {"kept_previous": True, "failures": payload["failures"]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", "utf-8")
    return {"posts": len(payload["posts"]), "failures": payload["failures"]}
