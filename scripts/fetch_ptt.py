#!/usr/bin/env python3
"""PTT 看板抓取 — 社群優惠情報（交叉驗證用）。

抓 5 個看板 index 的文章標題／推文數／連結，篩出與支付/優惠/額滿相關者。
這些是社群來源：時效快、能補官網抓不到的（如全家），也常有「額滿」第一手回報，
但未經官方確認 —— 一律標為社群、以各官方公告為準。

僅用 Python 標準庫：

    python scripts/fetch_ptt.py > ptt.json
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

BOARDS = ["lifeismoney", "MobilePay", "e-coupon", "CVS", "fastfood"]
UA = "Mozilla/5.0 (tw-pay-deals-radar; +https://github.com/garychen-soc/tw-pay-deals-radar)"
KEYWORDS = (
    "Pay", "pay", "支付", "回饋", "優惠", "折", "券", "額滿", "刷卡", "超商",
    "街口", "悠遊", "全支付", "icash", "LINE", "台灣Pay", "錢包", "點數",
)

R_ENT = re.compile(r'<div class="r-ent">(.*?)</div>\s*</div>\s*</div>', re.S)
R_TITLE = re.compile(r'<div class="title">\s*(?:<a href="([^"]+)">)?\s*([^<]*)', re.S)
R_PUSH = re.compile(r'<div class="nrec">(?:<span[^>]*>)?([^<]*)', re.S)


def fetch_board(board: str) -> list[dict]:
    url = f"https://www.ptt.cc/bbs/{board}/index.html"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", "ignore")

    posts = []
    for block in R_ENT.findall(html):
        tm = R_TITLE.search(block)
        if not tm:
            continue
        href, title = tm.group(1), (tm.group(2) or "").strip()
        if not title or title.startswith("("):  # 跳過「(本文已被刪除)」等
            continue
        pm = R_PUSH.search(block)
        posts.append({
            "board": board,
            "title": title,
            "url": ("https://www.ptt.cc" + href) if href else None,
            "push": (pm.group(1).strip() if pm else ""),
            "source": "PTT（社群，需查證）",
        })
    return posts


def relevant(posts: list[dict]) -> list[dict]:
    return [p for p in posts if any(k in p["title"] for k in KEYWORDS)]


if __name__ == "__main__":
    collected = []
    for board in BOARDS:
        try:
            collected += relevant(fetch_board(board))
        except Exception as exc:  # noqa: BLE001
            print(f"# {board} 抓取失敗：{exc}", file=sys.stderr)
    print(f"# PTT：共 {len(collected)} 篇相關貼文", file=sys.stderr)
    print(json.dumps(collected, ensure_ascii=False, indent=2))
