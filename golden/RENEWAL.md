# golden 換新流程

golden 是「官方原文人工核對的事實」（回饋、門檻、每人上限、已額滿、登錄、是否優惠），
`ops/run_local.sh` 每輪以它的精確率把關發布。每筆記著標註當時的條款內容雜湊；官方頁
改版或下架後那筆就不再計分（`payradar score` 的 `stale`／`not_found`）。支付優惠改版快
（上線後 4 天就有 5 筆失效），所以**每週一**由 Claude 例行任務 `tw-pay-radar-golden-renewal`
照本文件把失效的條目換新。每日排程在有效筆數低於 45 時也會發通知。

## 鐵則

1. **獨立性**：標註只讀官方原文副本 `var/golden_src/<id>.txt`。不得讀 `state/`、
   `web/public/data/`、`state/extractions/` 或任何抽取輸出 —— golden 抄自受測對象就失去意義。
2. **只動 golden**：只改 `golden/golden.yaml` 與 `var/golden_src/`。評分發現抽取錯誤時
   **不修程式**，列入報告交給 Gary 另開 session。
3. **逐字引用**：每個 quote 必須逐字出現在原文副本；`renew.py check` 必須 `RESULT: PASS`。
4. **不碰個資與登入**：只讀公開頁面。
5. 標註規格以 `golden/SPEC.md` 為準（含歷次仲裁後的定義）。

## 步驟

0. 前置
   - `cd ~/Projects/tw-pay-deals-radar && git switch main && git pull --rebase`
   - 確認沒有更新程序在跑：`pgrep -f "payradar run"` 無結果、`state/.run.lock` 不存在
     （有就等 5 分鐘再查，最多 30 分鐘；仍在跑就中止並回報）。
   - `var/docs/*.json` 的修改時間須在 36 小時內（每日 08:50 的排程會更新）；否則中止並回報。
1. `.venv/bin/python golden/renew.py select` —— 產生 `var/golden_src/renewal.json`（要標註的
   條目與原文副本路徑）。沒有要標註的就跳到步驟 6 回報「無需換新」。
2. 逐筆完整讀 `renewal.json` 列出的原文副本，依 `golden/SPEC.md` 標註，寫成
   `var/golden_src/renewal.yaml`（YAML 清單；每筆 `id` 照計畫，欄位同 golden.yaml：
   `is_offer`、`registration`、`rewards`、`min_spend`、`caps`、`sold_out`、`quotes`、`note`；
   `provider`／`url`／`title`／`content_hash` 由 apply 自動帶入）。`changed` 的條目要**整筆
   重新標註**，不要沿用舊值；`note` 寫「YYYY-MM-DD 換新」與判讀依據。拿不準就不記並在 note 說明。
3. `.venv/bin/python golden/renew.py apply` → `.venv/bin/python golden/renew.py check`
   必須 `RESULT: PASS`（WARN 可接受，但 sold_out／quote 錯誤要修 renewal.yaml 後重跑 apply）。
4. `.venv/bin/payradar score`：對換新條目的每個 `wrong`／`missed` 回原文逐字重讀。
   golden 錯 → 改 golden，`note` 寫更正理由；抽取錯 → golden 不動，記入報告
   （條目、golden 事實、抽取事實、原文 quote）。
5. `.venv/bin/python -m pytest -q` 全過後提交並推送：
   `git add golden && git commit -m "golden: 換新 N 筆（YYYY-MM-DD）" && git push`。
   推送前再確認一次步驟 0 的鎖；被拒就 `git pull --rebase` 後重推。
6. 回報：換新／新增／移除筆數、score 的精確率與召回率、golden 更正清單、抽取錯誤清單，
   並發 `osascript -e 'display notification "…" with title "台灣支付優惠雷達"'`。
