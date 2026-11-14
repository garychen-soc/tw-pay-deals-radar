# 涵蓋範圍與已知缺口（2026-11-06，11-14 更新台灣Pay）

| 服務 | 擷取器 | 實測筆數 | 方式 | 缺口／限制 |
|---|---|---|---|---|
| 全支付 | pxpayplus | 55 | 明細 API 逐 id 掃描 | 額滿公告只在 App 小鈴鐺，條款不會寫已額滿 |
| 台灣Pay | taiwanpay | 56 | TF020109／TF020110 API，**瀏覽器內呼叫** | 2026-11-14 起官網加 Citrix NetScaler，非瀏覽器一律 403，改由瀏覽器開官方頁後在頁內呼叫 API（Gary 核准）；說明頁型（template 3）條款多為圖片 |
| 一卡通 iPASS MONEY | ipassmoney | 37 | HTML 列表＋明細 | 8 筆條款在合作銀行網站（標 external，不抽取） |
| 悠遊付 | easywallet | 74 | HTML 列表＋明細 | 官網偶發整站 502（11-06 凌晨），沿用上一輪 |
| 街口 | jkopay | 36 | Next.js RSC 串流解析 | 10 筆連到合作方網站；6 筆無期間；含會員權益、捐款等非優惠頁（交 is_offer 判斷） |
| 歐付寶 | opay | 11 | banner 列表＋公告明細 | 列表無日期、不下架舊 banner，期間由條款「活動時間」補 |
| icash Pay | icashpay | 11 | 會員權益頁＋內嵌 JSON | 每張卡沒有獨立網址（都連列表頁）；期間為整區共用 |
| Pi 拍錢包 | pipay | 7 | WordPress 列表＋文章 | 沒寫年份的期間不解析；只翻到整頁都過期為止（上限 6 頁） |
| 全盈+PAY | pluspay | 13 | 官網月度懶人包 CSV＋月頁 | **懶人包月底才發布，當月新開的活動要等下個月才收得到**（舊版 25 筆）。活動站 event2023 是 19.5MB 版面元件樹，不採用 |
| 橘子支付 | gamapay | 4 | 官網 NewsList／News API | 只看近 210 天的活動貼文；超過且未重發的長期活動會漏 |
| 全聯 PX Pay | pxmart | 6 | `/campaign/{type}` 的 `__NEXT_DATA__` | 只收 PX Pay 與信用卡分類＋標題含 PX Pay／全支付的最新活動；福利任務、年菜預購不收 |

## 未納入

| 服務 | 原因 |
|---|---|
| OPEN 錢包 | 擷取器已寫（`providers/openpoint.py`）但不註冊：實測 3 筆全是 icash Pay 活動、條款連到 icash，與 icashpay 重複 |
| 全家 FamiPay | 沒有統一活動列表，活動散在 `nevent.family.com.tw` 各專頁；全盈+PAY 的全家活動已涵蓋 |
| LINE Pay | 活動頁只有一張導向 App 內頁的長期卡片，沒有具體優惠 |
| 簡單付 ezPay | 舊版就沒有收錄 |

跨服務重複：同一個 TWQR 商圈活動會同時出現在台灣Pay 與各電子支付（例如十大商圈、新社大安），
全聯週年慶在全聯官網與全支付各一頁。各服務的條款與回饋不同，因此各自保留，不跨服務合併。
