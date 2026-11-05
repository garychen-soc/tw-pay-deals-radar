# golden 標註規格（支付優惠）

每筆 golden 對應一個官方活動頁，**只依原文副本 `var/golden_src/<id>.txt` 判斷**，
不讀 `state/`、`state/extractions/`、`var/docs/` 或任何程式輸出。

## 要標的事實（以整頁為單位，不分子活動）

| 欄位 | 內容 | 記法 |
|---|---|---|
| `is_offer` | 這頁是不是給消費者的優惠活動（有回饋、折扣、贈品、抽獎） | yes / no |
| `registration` | 參加前是否必須主動登錄／報名／領券／加入點數計畫／切換權益／輸入代碼（單純綁定付款工具、下載 APP 不算）。**任一子活動需要就記 required**；條款完全沒提任何這類動作記 not_required | required / not_required / unknown（原文沒寫清楚） |
| `rewards` | 消費者得到的每一種回饋（禮包內含的各張券也逐項記） | `{unit, value}`；unit ∈ `%` `元` `點` `折` `other`；折扣、贈品記 `other`／null（評分只比有無）；只寫「享回饋」沒數字 value=null |
| `min_spend` | 參加門檻 | `{amount, unit}`（「單筆滿1,200元」→ 1200 元；「付款任3筆」→ 3 筆） |
| `caps` | 每人（每帳戶）回饋上限，含單筆上限 | `{amount, unit}`（「每人每月上限200點」→ 200 點；「單筆最高25點」→ 25 點；「每人限贈1份」→ 1 份；次數類單位 次／份／筆 評分時視為同類）。活動總預算／總名額、抽獎機會次數**不是**每人上限 |
| `sold_out` | 官方「已經」額滿／已達上限／已贈完的聲明 | `{scope_label, scope_start, scope_end}`（ISO 日期；整檔活動三者皆空；有範圍字樣但無日期如「首週」只填 scope_label）＋`quote`。「額滿為止」「名額額滿將於…公告」「如額滿」是條件句，**不算** |

數值一律照原文（「1,000元」→ 1000、「0.5%」→ 0.5、「2萬份」→ 20000），不換算、不加總。
同一事實在頁面重複出現只記一次。頁面列了多家銀行／多個子活動各自的回饋，全部都記。
範圍只寫月份的額滿（「11月份…已額滿」）→ 該月 1 日到月底；年份依活動期間判斷。

## 輸出格式（YAML 清單）

```yaml
- id: easywallet-01
  provider: easywallet
  url: <manifest 的 url>
  content_hash: <manifest 的 content_hash>
  is_offer: yes
  registration: not_required
  rewards: [{unit: "%", value: 3}, {unit: 元, value: 100}]
  min_spend: [{amount: 5000, unit: 元}]
  caps: [{amount: 50, unit: 元}]
  sold_out:
    - {scope_start: null, scope_end: null, quote: "【已於10/28(三)00:03額滿】"}
  quotes: ["支撐判斷的原文句子，逐字"]
  note: "不確定或判讀依據"
```

拿不準的事實寧可不記並在 note 說明 —— golden 錯一筆，評分就會冤枉或放過抽取器。
