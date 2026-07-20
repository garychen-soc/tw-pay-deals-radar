#!/usr/bin/env python3
"""台灣Pay 活動抓取 — 後端 JSON API（解析框架）。

台灣Pay 官網是 SPA、活動內容是 PNG banner，但背後有 JSON 列表 API：

    POST https://www.taiwanpay.com.tw/tpay/v1.0.0/950/taiwanpayfapi/TF02/TF020109
      → body.recommendedCampaigns[]：systemSeq / title / startDate / endDate / paymentType
      → title 含「(活動額滿)」「每月額滿」＝官方額滿標記
        （這是全台各支付服務中，唯一能「明確判定已額滿」的資料源）

⚠️ 此 POST 需要正確的 request header/body（txSn 等）；空 body 會回
   TF9999「系統忙碌中」。最穩定的做法是由 Claude 排程用瀏覽器開活動頁、
   透過 read_network_requests 擷取實際 request 後解析回應。
   本檔提供 parse()／額滿判定，payload 取得後即可直接餵入。
"""
from __future__ import annotations

import json

LIST_API = "https://www.taiwanpay.com.tw/tpay/v1.0.0/950/taiwanpayfapi/TF02/TF020109"
DETAIL_API = "https://www.taiwanpay.com.tw/tpay/v1.0.0/950/taiwanpayfapi/TF02/TF020110"

FULL_MARKERS = ("(活動額滿)", "（活動額滿）", "每月額滿")


def is_full(title: str) -> bool:
    """依官方標題判定是否已額滿。"""
    return any(mark in title for mark in FULL_MARKERS)


def parse(body: dict) -> list[dict]:
    """把 TF020109 回應的 body 解析成標準活動清單。"""
    campaigns = []
    for c in body.get("recommendedCampaigns", []):
        title = c.get("title", "")
        campaigns.append({
            "service": "台灣Pay",
            "systemSeq": c.get("systemSeq"),
            "title": title,
            "start": c.get("startDate"),
            "end": c.get("endDate"),
            "full": is_full(title),
        })
    return campaigns


if __name__ == "__main__":
    # 示範：把瀏覽器擷取到的回應存成 taiwanpay_raw.json 後解析
    import pathlib
    sample = pathlib.Path("taiwanpay_raw.json")
    if sample.exists():
        body = json.loads(sample.read_text("utf-8")).get("body", {})
        print(json.dumps(parse(body), ensure_ascii=False, indent=2))
    else:
        print(__doc__)
