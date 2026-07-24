"""測試 scripts/ 的純函式邏輯（不需網路）。"""
import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_pxpayplus  # noqa: E402
import fetch_taiwanpay  # noqa: E402
import fetch_ptt  # noqa: E402
import verify_links  # noqa: E402


class TestFetchPxpayplus(unittest.TestCase):
    def test_not_expired_filters_past(self):
        today = datetime.date(2026, 7, 21)
        items = [
            {"end": "2026-08-31"},   # 未過期
            {"end": "2026-07-01"},   # 已過期
            {"end": "2026/12/31"},   # 斜線格式、未過期
            {"end": None},           # 日期不明，保留
        ]
        ends = [i["end"] for i in fetch_pxpayplus.not_expired(items, today)]
        self.assertIn("2026-08-31", ends)
        self.assertIn("2026/12/31", ends)
        self.assertIn(None, ends)
        self.assertNotIn("2026-07-01", ends)


class TestFetchTaiwanpay(unittest.TestCase):
    def test_is_full_detects_official_markers(self):
        self.assertTrue(fetch_taiwanpay.is_full("(活動額滿)TWQR金門風華Pay享10%回饋"))
        self.assertTrue(fetch_taiwanpay.is_full("（活動額滿）誠品生活"))
        self.assertTrue(fetch_taiwanpay.is_full("TWQR花火狂歡【每月額滿詳活動提醒】"))

    def test_is_full_ignores_normal_titles(self):
        self.assertFalse(fetch_taiwanpay.is_full("延三夜市 單筆 30% 回饋"))
        self.assertFalse(fetch_taiwanpay.is_full("限量送完為止"))  # 限量≠額滿

    def test_parse_maps_marker(self):
        body = {"recommendedCampaigns": [
            {"systemSeq": "X1", "title": "(活動額滿)誠品生活", "startDate": "2026-06-04", "endDate": "2026-08-31"},
            {"systemSeq": "X2", "title": "延三夜市 30%", "startDate": "2026-07-01", "endDate": "2026-08-31"},
        ]}
        out = fetch_taiwanpay.parse(body)
        self.assertTrue(out[0]["full"])
        self.assertFalse(out[1]["full"])


class TestFetchPtt(unittest.TestCase):
    def test_relevant_filters_by_keyword(self):
        posts = [
            {"title": "[情報] 街口飲料節 最高42.5%回饋"},
            {"title": "[閒聊] 今天天氣真好"},
            {"title": "[情報] LINE Pay 乘車碼額滿"},
        ]
        titles = [p["title"] for p in fetch_ptt.relevant(posts)]
        self.assertEqual(len(titles), 2)
        self.assertNotIn("[閒聊] 今天天氣真好", titles)


class TestVerifyLinksClassify(unittest.TestCase):
    """連結判定純邏輯（不需網路）。樣本取自 2026-07-25 真瀏覽器實測結果。"""

    TP_NEW = "https://www.taiwanpay.com.tw/fisc-tpay/news/event/X"
    TP_OLD = "https://www.taiwanpay.com.tw/tpay/news/event/X"
    PX_OLD = "https://www.pxpayplus.com/activity_content_page?EventId=1"
    PX_NEW = "https://marketing.pxpayplus.com/pxplus_marketing_page/activity_content_page?EventId=1"
    YU = "https://easywallet.easycard.com.tw/benefit/content?id=1"
    IPASS = "https://www.i-pass.com.tw/Preferential/Detail/X"
    PI = "https://web.piapp.com.tw/events-x/"
    ICASH = "https://www.icashpay.com.tw/event/memberbenefits/"

    def s(self, title, body, url):
        return verify_links.classify(title, body, url)[0]

    def test_taiwanpay_ok_when_title_has_activity(self):
        self.assertEqual(self.s("台灣Pay | 台灣Pay揪OK 筆筆10%回饋", "", self.TP_NEW), "ok")

    def test_taiwanpay_dead_on_not_found(self):
        self.assertEqual(self.s("台灣Pay", "很抱歉，找不到頁面 請再試一次", self.TP_OLD), "dead")

    def test_taiwanpay_dead_on_bare_brand_title(self):
        self.assertEqual(self.s("台灣Pay", "", self.TP_OLD), "dead")

    def test_pxpayplus_dead_on_bare_brand_title(self):
        # 導回官網首頁：title 為純品牌名，即使內文含「兌換/回饋」也判 dead
        self.assertEqual(self.s("全支付", "全面支持你付 點數兌換 回饋", self.PX_OLD), "dead")

    def test_pxpayplus_ok_on_activity_page(self):
        self.assertEqual(self.s("全支付活動頁", "十大商圈 活動日期", self.PX_NEW), "ok")

    def test_easycard_waf_is_warn_not_dead(self):
        self.assertEqual(self.s("The URL you requested was rejected", "", self.YU), "warn")

    def test_easycard_502_is_warn_not_dead(self):
        self.assertEqual(self.s("502 Bad Gateway", "", self.YU), "warn")

    def test_easycard_ok_with_activity_content(self):
        self.assertEqual(self.s("金門海灘花蛤季", "活動內容 活動期間 2026", self.YU), "ok")

    def test_easycard_home_is_warn_not_dead(self):
        # 導回悠遊付首頁（無 404 詞、無活動詞）→ warn 保留，不誤刪
        self.assertEqual(self.s("悠遊付｜一卡一付", "認識悠遊付 功能說明 下載專區", self.YU), "warn")

    def test_ipass_ok(self):
        self.assertEqual(self.s("【公路養管費】使用 iPASS MONEY", "活動期間 注意事項", self.IPASS), "ok")

    def test_pi_ok(self):
        self.assertEqual(self.s("Pi 拍錢包 綁玉山", "一、活動時間 二、活動內容", self.PI), "ok")

    def test_icash_list_ok(self):
        self.assertEqual(self.s("2026 icash Pay 會員專屬優惠", "優惠內容", self.ICASH), "ok")

    def test_never_dead_on_transient_or_waf(self):
        # 核心防誤刪保證：任何明細站遇 5xx/WAF 都絕不可 dead
        for url in (self.YU, self.IPASS, self.PI, self.TP_NEW, self.PX_NEW):
            self.assertNotEqual(self.s("502 Bad Gateway", "", url), "dead", url)
            self.assertNotEqual(self.s("Access Denied", "", url), "dead", url)


if __name__ == "__main__":
    unittest.main()
