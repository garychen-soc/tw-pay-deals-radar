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
      已額滿 100%／100%；首次全量 216 筆、LLM 約 US$7.4）
- [ ] Phase 3：長尾服務擷取器
- [ ] Phase 4：Astro 前端、行事曆、PTT 社群區
- [ ] 切換：merge main、launchd、停用舊例行任務

## 開發

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests && .venv/bin/mypy
.venv/bin/payradar probe pxpayplus      # 單一服務擷取體檢（會連官網）
```

## 安全邊界

只讀取公開官方資訊：不登入、不領券、不代使用者登錄、不解 CAPTCHA。改用一般瀏覽器
UA 的服務逐家核准並寫明理由。實際回饋與名額以各支付服務官方公告為準。
