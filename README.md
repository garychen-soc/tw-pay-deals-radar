# 台灣支付優惠雷達 v2

台灣行動支付（全支付、台灣Pay、一卡通 iPASS MONEY、悠遊付、街口…）官方優惠監控。
v2 參照 [刷卡登錄雷達](https://github.com/garychen-soc/tw-card-registration-radar)
的架構重建，回答使用者真正的三個問題：

1. **拿得到多少**：回饋率／固定金額、每人上限、門檻、適用通路與付款方式。
2. **還有沒有名額**：官方明寫已額滿才算額滿；「限量／送完為止」只是限量。
3. **要不要先登錄或領券**：何時開放、先登錄再消費還是可事後補。

## 三條規則（沿用刷卡登錄雷達）

- **只放事實，不放推導。** 進行中／即將截止／高回饋等「今天」的函數由前端計算。
- **抓不到就留空，絕不填補。**
- **有風險的欄位帶逐字原文證據。** LLM 只負責「找出哪一句」，數值與日期由程式
  驗證引句逐字出現在條款裡才採用。

## 架構

```
providers/<服務>.py  discover() → Stub（標題只取結構化欄位）
                     terms(stub) → Terms（條款全文，限縮在內容容器）
        ↓
extract/  claude -p --json-schema 定位（內容雜湊快取，條款沒變不重算）→ 程式驗證
        ↓
state/  唯一正本（進版控）→ emit → web/（Astro）＋ 行事曆
```

排程：本機 launchd 每日一次（`ops/run_local.sh`），任何一步失敗或 golden 精確率
不達標就不發布，以 macOS 通知回報。

## 進度

- [x] Phase 1：套件骨架、transport、四大宗擷取器（全支付 55／台灣Pay 52／
      一卡通 37＋外部 8／悠遊付 74，2026-11-06 實測）
- [x] Phase 2：LLM 定位 schema、程式驗證、golden 集（36 頁；精確率 98.7%／召回率 90.5%，
      已額滿 100%／100%；首次全量 217 筆、LLM 約 US$7.4）
- [x] Phase 3：長尾 7 家擷取器（街口 36／歐付寶 11／icash 11／Pi 7／全盈 13／橘子 4／全聯 6）；
      golden 擴至 57 頁，精確率 98.9%／召回率 87.8%；缺口見 `docs/coverage.md`
- [x] Phase 4：Astro 前端（優惠／登錄時程／社群／來源四分頁，手機與深淺色走查）、
      行事曆 `calendar/deals.ics`、PTT 社群區、deploy／verify workflow、`ops/run_local.sh`＋launchd plist（未安裝）
- [x] 切換上線（2026-11-10）

## 上線狀態（2026-11-10 切換）

- GitHub Pages 來源＝GitHub Actions（`deploy.yml`），v1 的根目錄 `calendar.ics` 仍同步輸出。
- 本機 launchd `com.garychen.tw-pay-radar` 每日 08:50（`ops/run_local.sh`），log 在
  `~/Library/Logs/tw-pay-radar/`；首次試跑 3 分鐘、LLM 17 次約 US$1。
- 舊的 Claude 例行任務 `tw-pay-promo-monitor` 已停用。

## 自我修復（2026-11-14）

| 層 | 觸發 | 自動處理 | 通知 |
|---|---|---|---|
| 排程 | 08:50 沒成功（網路、睡眠、額度、任何錯誤） | 12:50、18:50 補跑；當天已成功就跳過 | 失敗必通知 |
| 排程 | 上一輪殘留資料、push 被拒、Chromium 不見 | 清除、rebase 重推、重裝 | — |
| 來源 | HTTP 403／429 | 改用瀏覽器重抓一次 | 有；連續 3 天建議永久改用 |
| 來源 | 逾時、連線、5xx | 本輪結尾重試一次 | 有 |
| 程式 | 修復後仍失敗、筆數驟降、明細大量失敗 | 背景開 Claude 修擷取器（`ops/repair.sh`），獨立分支＋PR，**不改 main** | 有；同一來源 3 天冷卻 |

紀錄檔（只在本機）：`var/last_success`、`var/fallback_history.json`、`var/repairs.json`；
修復 session 日誌在 `~/Library/Logs/tw-pay-radar/repair-*.log`，worktree 在
`~/Projects/tw-pay-deals-radar-repair/`（沒開 PR 會自動清掉）。

## 開發

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests && .venv/bin/mypy
.venv/bin/payradar probe pxpayplus      # 單一服務擷取體檢（會連官網）
.venv/bin/payradar emit && npm --prefix web run build   # 從 state 重產網站並建置
```

## 安全邊界

只讀取公開官方資訊：不登入、不領券、不代使用者登錄、不解 CAPTCHA。改用一般瀏覽器
UA 的服務逐家核准並寫明理由。實際回饋與名額以各支付服務官方公告為準。
