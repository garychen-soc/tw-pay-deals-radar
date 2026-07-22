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


if __name__ == "__main__":
    unittest.main()
