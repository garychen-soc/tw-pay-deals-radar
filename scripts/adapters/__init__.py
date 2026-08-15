"""確定性抓取 adapter（C 架構核心引擎）。

每個 adapter 提供 fetch(today) -> (activities, stats)：
- activities：符合 promotions.json schema、由代碼確定性產出（含 evidence），不靠 AI 現場判斷。
- stats：{provider, official_ok, official_expected, extended_ok, extended_expected, errors, live}

大宗且常踩雷的服務（全支付/台灣Pay/一卡通…）走 adapter 消除波動；長尾與 PTT 仍由排程 AI 補。
"""
