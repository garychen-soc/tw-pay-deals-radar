#!/usr/bin/env python3
"""用 agent-browser 開真瀏覽器實測 data/promotions.json 每個活動連結是否可達。

為什麼需要它：台灣Pay/全支付等是 SPA，deep link 即使 HTTP 回 200 也可能是空殼或
被導回官網首頁——curl/urllib 判不出來（2026-07-25 曾因此漏掉 76 筆死連結）。
本腳本開 Chrome（via agent-browser）抓「渲染後的 title + 內文」判死活，真正反映使用者點擊結果。

判定分級（避免誤殺好連結）：
- 明細型服務（台灣Pay/全支付/悠遊付/一卡通/Pi）：連結指向單一活動，頁面須含活動標誌詞
  （活動時間/活動日期/回饋…），否則判 dead（被導回首頁或 404）。
- 列表/官網型服務（icash/街口/歐付寶/全盈/LINE/OPEN/全家/橘子/全聯）：連結本就指向列表或
  官網，只要能開、內文非空、且無錯誤詞即算 ok。
- 任何服務：title/內文明確出現「找不到/404/not found/錯誤」→ dead。
- 逾時或 eval 失敗 → warn（不自動剔除，留給人看）。

用法：
  python3 scripts/verify_links.py               # 只報告
  python3 scripts/verify_links.py --fix         # 報告並把 dead 連結的活動移出 JSON
  python3 scripts/verify_links.py --limit 15    # 只驗前 15 個 unique 連結（除錯/抽樣）
  python3 scripts/verify_links.py --only 台灣Pay # 只驗某服務

需求：本機已安裝 agent-browser（npm i -g agent-browser）。相同 url 只驗一次（去重）。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "promotions.json"

# 明細型：連結指向「單一活動明細」，須確認真的連到活動（非導回首頁/404）
DETAIL_HOSTS = ("taiwanpay.com.tw", "pxpayplus.com", "easywallet.easycard.com.tw",
                "i-pass.com.tw", "web.piapp.com.tw")
# title 型服務：內文是圖片或 SPA 不穩，改看 title——渲染後 title 若「等於純品牌名」＝沒連到活動
#（台灣Pay 好頁 title「台灣Pay | 揪OK…」；壞頁 title「台灣Pay」。全支付好頁「全支付活動頁」；壞頁「全支付」）
HOME_TITLES = {"taiwanpay.com.tw": "台灣Pay", "pxpayplus.com": "全支付"}
# 明確「找不到/錯誤」訊號（大小寫不敏感）
DEAD_WORDS = ("找不到", "404", "not found", "無此", "頁面不存在", "page not found", "查無")
# WAF/bot 攔截頁特徵：headless 常被 easycard 等站的 WAF 擋（≠死連結，勿誤刪）
WAF_WORDS = ("the url you requested", "was rejected", "consult with your administrator",
             "access denied", "forbidden", "拒絕存取", "已被拒絕", "verify you are human",
             "are you a human", "請完成驗證", "cloudflare")
# 暫時性伺服器錯誤：稍後會恢復，絕不可當死連結（≠dead）
TRANSIENT_WORDS = ("502 bad gateway", "503 service", "500 internal", "bad gateway",
                   "service unavailable", "504 gateway", "gateway timeout", "暫時無法",
                   "系統忙碌", "please try again")
# 內文可靠的明細型服務（悠遊付/一卡通/Pi）：頁面至少應含其一結構詞
ACTIVITY_MARKS = ("活動時間", "活動期間", "活動日期", "活動內容", "活動對象",
                  "活動辦法", "活動說明", "活動地點", "注意事項", "回饋")


def ab(args, timeout=40):
    """呼叫 agent-browser，回傳 CompletedProcess。"""
    return subprocess.run(["agent-browser", *args], capture_output=True,
                          text=True, timeout=timeout)


def classify(title, body, url):
    """純判定邏輯（無網路，方便單元測試）。回傳 (status, note)。status ∈ ok|dead|warn。

    安全優先：只有鐵證才判 dead（可自動剔除）；一切模糊（WAF/5xx/無內容）一律 warn 保留。
    """
    title = (title or "").strip()
    body = (body or "").strip()
    hay = (title + "\n" + body).lower()

    # 1) 明確錯誤詞 → 高信度 dead（鐵證）
    if any(w.lower() in hay for w in DEAD_WORDS):
        return ("dead", "頁面回報找不到/錯誤")
    # 1.5) WAF/bot 攔截 → warn（headless 被擋，非死連結，勿誤刪；需 --headed/--profile 或人工）
    if any(w in hay for w in WAF_WORDS):
        return ("warn", "疑遭 WAF/bot 攔截，需 --headed 或人工確認")
    # 1.6) 暫時性 5xx 錯誤 → warn（稍後重試，勿誤刪）
    if any(w in hay for w in TRANSIENT_WORDS):
        return ("warn", "伺服器暫時性錯誤（5xx），稍後重試")
    # 2) title 型服務（台灣Pay/全支付）：title == 純品牌名 = 沒連到活動（鐵證，可 dead）
    for host, home in HOME_TITLES.items():
        if host in url:
            if title == home or not title:
                return ("dead", f"title 為純品牌名「{home}」（導回首頁/404）")
            return ("ok", "")
    # 3) 內文可靠的明細型（悠遊付/一卡通/Pi）：有活動結構詞＝ok；無＝warn（不確定，不自動刪）
    if any(h in url for h in DETAIL_HOSTS):
        if body and any(m in body for m in ACTIVITY_MARKS):
            return ("ok", "")
        return ("warn", "明細頁未見活動內容，需人工確認（勿逕自剔除）")
    # 4) 列表/官網型：能開、內文非空即 ok；空則 warn
    if not body:
        return ("warn", "頁面內文為空")
    return ("ok", "")


def probe(url):
    """開真瀏覽器實測單一連結，回傳 (status, title, note)。"""
    try:
        ab(["open", url], timeout=40)
        ab(["wait", "2500"], timeout=8)  # 等 SPA 渲染 title/內文（台灣Pay/全支付延遲載入）
        expr = ("JSON.stringify({t:document.title,"
                "b:document.body.innerText.slice(0,800),u:location.href})")
        r = ab(["eval", expr, "--json"], timeout=20)
        outer = json.loads(r.stdout.strip())
        if not outer.get("success"):
            return ("warn", "", f"eval 失敗:{outer.get('error')}")
        info = json.loads(outer["data"]["result"])
    except subprocess.TimeoutExpired:
        return ("warn", "", "逾時")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        return ("warn", "", f"解析失敗:{e}")

    title = (info.get("t") or "").strip()
    status, note = classify(title, info.get("b"), url)
    return (status, title, note)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="剔除判定為 dead 的活動並寫回 JSON")
    ap.add_argument("--limit", type=int, default=0, help="只驗前 N 個 unique 連結")
    ap.add_argument("--only", default="", help="只驗指定 provider_name")
    args = ap.parse_args()

    d = json.loads(DATA.read_text("utf-8"))
    acts = d["activities"]

    # 去重：unique url → 代表用的 provider_name（僅供顯示）
    url2name = {}
    for a in acts:
        if args.only and a["provider_name"] != args.only:
            continue
        url2name.setdefault(a["url"], a["provider_name"])
    urls = list(url2name)
    if args.limit:
        urls = urls[: args.limit]

    print(f"實測 {len(urls)} 個 unique 連結"
          f"（活動總數 {len(acts)}）…\n")
    result = {}
    dead, warn = [], []
    for i, u in enumerate(urls, 1):
        status, title, note = probe(u)
        result[u] = status
        icon = {"ok": "✅", "dead": "❌", "warn": "⚠️"}[status]
        line = f"[{i:>3}/{len(urls)}] {icon} {url2name[u]}｜{title[:30]}"
        if note:
            line += f"｜{note}"
        if status != "ok":
            line += f"\n        {u}"
            (dead if status == "dead" else warn).append((url2name[u], u, note))
        print(line)
    try:
        ab(["close"], timeout=15)
    except Exception:
        pass

    ok = sum(v == "ok" for v in result.values())
    print(f"\n===== 結果：ok {ok}｜dead {len(dead)}｜warn {len(warn)} =====")
    if dead:
        print("\n❌ 死連結：")
        for name, u, note in dead:
            print(f"  {name}｜{note}｜{u}")
    if warn:
        print("\n⚠️ 待確認：")
        for name, u, note in warn:
            print(f"  {name}｜{note}｜{u}")

    if args.fix:
        if dead:
            dead_urls = {u for _, u, _ in dead}
            removed = [a["id"] for a in acts if a["url"] in dead_urls]
            d["activities"] = [a for a in acts if a["url"] not in dead_urls]
            rid = set(removed)
            d["featured_ids"] = [i for i in d.get("featured_ids", []) if i not in rid]
            DATA.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
            print(f"\n🔧 --fix：已剔除 {len(removed)} 筆死連結活動 → {removed}")
        else:
            print("\n🔧 --fix：無死連結，無需剔除")
        sys.exit(0)  # 修復模式：已處理完畢，正常結束
    # 純檢查模式：有死連結以非 0 結束，方便 CI/排程判斷
    sys.exit(1 if dead else 0)


if __name__ == "__main__":
    main()
