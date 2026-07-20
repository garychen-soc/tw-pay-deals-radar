# 📡 台灣行動支付優惠雷達 · tw-pay-deals-radar

每日彙整台灣 **15 家行動支付／電子支付** 的官方優惠活動，一頁手機網站掃完
**即將截止、已額滿、高回饋**，並排除過期活動、標記未過期但已額滿者。

> 🌐 **網站**：https://garychen-soc.github.io/tw-pay-deals-radar/
> （隔日自動更新；下方為運作方式）

---

## 涵蓋範圍

**台灣 10 家專營電子支付機構**（金管會核准）：
歐付寶、橘子支付、簡單付、街口、悠遊付（悠遊卡）、一卡通 iPASS MONEY、
icash Pay（愛金卡）、全盈+PAY、全支付、LINE Pay（連加）

**＋ 5 家主流錢包／點數**：台灣Pay、Pi 拍錢包、全聯 PX Pay、全家 FamiPay、OPEN錢包

**＋ 5 個 PTT 社群看板**（交叉驗證、補官網抓不到的）：
[lifeismoney](https://www.ptt.cc/bbs/lifeismoney/index.html)、
[MobilePay](https://www.ptt.cc/bbs/MobilePay/index.html)、
[e-coupon](https://www.ptt.cc/bbs/e-coupon/index.html)、
[CVS](https://www.ptt.cc/bbs/CVS/index.html)、
[fastfood](https://www.ptt.cc/bbs/fastfood/index.html)

## 運作架構（Claude 分析 ＋ GitHub 託管）

這些來源的抓取難度差異極大，因此採「智能分析 ＋ 靜態託管」：

| 環節 | 做法 |
|---|---|
| **抓取** | 台灣Pay／全支付有後端 JSON API（見 `scripts/`）；街口／悠遊付／橘子是 SPA 需瀏覽器渲染；全家有 bot 防護；PTT 為 HTML |
| **判讀** | 「額滿雙來源判定、日期分類、跨來源去重、PTT 社群交叉驗證」由 Claude 隔日分析完成 |
| **產出** | 依 `site_template.html` 版型產生 `index.html`（手機友善、雙主題），push 到本 repo |
| **託管** | GitHub Pages 自動發佈網站；每次 commit 即更新 |

> 為何不做成純爬蟲 GitHub Actions？SPA 渲染與「額滿雙來源」等判讀，純規則式會明顯掉品質、且官網一改版就壞。此架構保留判讀品質，GitHub 提供公開網址與版本歷史。

## `scripts/` — 可程式化來源的參考實作

| 檔案 | 說明 | 可直接執行 |
|---|---|---|
| `fetch_pxpayplus.py` | 全支付後端 API 增量掃描（id 1→末端，`code` 判斷有效／末端） | ✅ 純標準庫，已實測 |
| `fetch_taiwanpay.py` | 台灣Pay 列表 API 解析 ＋ 官方額滿標記判定 | 解析框架（POST payload 需擷取） |
| `fetch_ptt.py` | 5 個 PTT 看板 index 抓取 ＋ 關鍵字篩選 | ✅ 純標準庫 |

```bash
# 無需安裝任何套件（僅用 Python 標準庫）
python scripts/fetch_pxpayplus.py    # 輸出全支付當前未過期活動 JSON
python scripts/fetch_ptt.py          # 輸出 PTT 相關貼文 JSON
```

## 抓取對策速查（實測）

- **台灣Pay**：`POST …/taiwanpayfapi/TF02/TF020109` → `recommendedCampaigns[]`；
  title 含「(活動額滿)」= 官方額滿標記（唯一能明確判定已額滿的來源）。
- **全支付**：`GET service.pxpayplus.com/px-advertise/web/activity/detail/{id}`；
  `code==0000` 有效、`code==2001` 末端。**勿用 prod-s3 的 `MKT_Event/event{id}.json`（2024 舊快取）**。
- **悠遊付／悠遊卡**：三來源 —— benefit（檔期）、easycard.com.tw/offers（特約）、
  easycard.com.tw/news（額滿公告）。

## 判讀規則

- 結束日 < 今日 → 剔除
- 開始日 > 今日 → 🔜 即將開跑
- 結束日距今 ≤ 7 天 → ⏰ 即將截止
- 官方明確額滿且未過期 → 🔴 已額滿（保留提醒別白跑）
- 僅「限量／名額 N／送完為止」未確認 → ⚠️ 限量

## 免責

本專案為**資訊整理**，非投資或消費建議。回饋率多為「最高」值，實際依各活動條件、
通路與登錄狀態而定，**消費前請以各支付官方活動辦法為準**。PTT 為社群來源，未經官方確認。
資料來自各支付公開官網／API 與公開看板。

## 授權

[MIT](./LICENSE) · © 2026 Gary Chen（garychen-soc）
