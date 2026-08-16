"""測試 adapter base 純函式（不需網路）——C 架構把知識固化進可測代碼的體現。"""
import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from adapters import base, taiwanpay, ipass, easywallet  # noqa: E402


class TestAdapterBase(unittest.TestCase):
    def test_iso_date(self):
        self.assertEqual(base.iso_date("2026/04/15 00:00"), "2026-04-15")
        self.assertEqual(base.iso_date("2026-12-31"), "2026-12-31")
        self.assertIsNone(base.iso_date(""))
        self.assertIsNone(base.iso_date("待確認"))

    def test_lifecycle(self):
        today = datetime.date(2026, 8, 5)
        self.assertEqual(base.lifecycle_of("2026-09-01", "2026-10-01", today), "upcoming")
        self.assertEqual(base.lifecycle_of("2026-07-01", "2026-10-01", today), "active")
        self.assertEqual(base.lifecycle_of(None, "2026-10-01", today), "active")  # 無起始=進行中

    def test_extract_reward(self):
        self.assertEqual(base.extract_reward("單筆享10%回饋"), ("10%", True))
        self.assertEqual(base.extract_reward("最高52% 或 5%"), ("最高52%", True))
        self.assertEqual(base.extract_reward("滿額享5%"), ("5%", False))       # <10% 非高回饋
        self.assertEqual(base.extract_reward("消費現折50元"), ("$50", False))   # <100 元
        self.assertEqual(base.extract_reward("免手續費"), ("", False))          # 無回饋數字

    def test_make_evidence_truncates(self):
        e = base.make_evidence("https://x", "很長" * 100, "news")
        self.assertEqual(e["kind"], "news")
        self.assertLessEqual(len(e["excerpt"]), 120)
        self.assertIn("observed_at", e)
        self.assertEqual(e["source_url"], "https://x")

    def test_build_activity_schema_defaults(self):
        a = base.build_activity(id="x-1", provider_id="x", provider_name="X",
                                title="T", url="https://x")
        for k in ("id", "provider_id", "provider_name", "title", "url", "lifecycle",
                  "quota_status", "evidence", "date_confidence", "review_required",
                  "is_high_return", "quota_evidence_complete"):
            self.assertIn(k, a)
        self.assertEqual(a["quota_status"], "not_marked_full")
        self.assertEqual(a["evidence"], [])
        self.assertEqual(a["channel"], "X")  # channel 預設用 provider_name


class TestTaiwanpayQuota(unittest.TestCase):
    def test_sold_out_whole(self):
        for t in ["(活動額滿)TWQR金門風華", "（活動額滿）誠品生活", "TWQR花火狂歡【活動額滿】"]:
            self.assertEqual(taiwanpay.quota_of(t), ("sold_out", True), t)

    def test_partial_monthly(self):
        self.assertEqual(taiwanpay.quota_of("TWQR花火【每月額滿詳提醒】"), ("partial_sold_out", True))

    def test_normal_not_full(self):
        self.assertEqual(taiwanpay.quota_of("台灣Pay揪OK 筆筆10%回饋"), ("not_marked_full", False))
        self.assertEqual(taiwanpay.quota_of("限量送完為止"), ("not_marked_full", False))  # 限量≠額滿


class TestIpassParse(unittest.TestCase):
    def test_parse_merges_title_and_date(self):
        # 同 id 兩塊：圖塊帶標題、文字塊帶日期——需合併
        html = ('<a href="/Preferential/Detail/ABC123"><img alt="【測試】活動 15% 回饋"></a>'
                '<a href="/Preferential/Detail/ABC123">【測試】活動 15% 回饋 '
                '2026/7/1 (三) ~ 2026/9/30 (三)</a>')
        items = ipass.parse_page(html)
        self.assertEqual(len(items), 1)
        aid, title, start, end = items[0]
        self.assertEqual(aid, "ABC123")
        self.assertIn("測試", title)
        self.assertEqual(start, "2026-07-01")
        self.assertEqual(end, "2026-09-30")

    def test_quota(self):
        self.assertEqual(ipass.quota_of("回饋上限已額滿")[0], "sold_out")
        self.assertEqual(ipass.quota_of("每月上限額滿")[0], "partial_sold_out")
        self.assertEqual(ipass.quota_of("一般活動 10%")[0], "not_marked_full")


class TestEasywalletParse(unittest.TestCase):
    def test_parse(self):
        html = ('<a href="/benefit/content.php?id=123" class="slider-card">'
                '<div class="card-text-block"><p class="title">悠遊付暢遊金門 23%</p>'
                '<p class="date">2026-08-01 － 2026-11-30</p></div></a>')
        items = easywallet.parse_page(html)
        self.assertEqual(len(items), 1)
        aid, title, start, end = items[0]
        self.assertEqual(aid, "123")
        self.assertIn("金門", title)
        self.assertEqual(start, "2026-08-01")
        self.assertEqual(end, "2026-11-30")

    def test_quota(self):
        self.assertEqual(easywallet.quota_of("每月額滿")[0], "partial_sold_out")
        self.assertEqual(easywallet.quota_of("本活動已額滿")[0], "sold_out")
        self.assertEqual(easywallet.quota_of("一般 10%")[0], "not_marked_full")


if __name__ == "__main__":
    unittest.main()
