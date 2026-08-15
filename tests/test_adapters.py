"""測試 adapter base 純函式（不需網路）——C 架構把知識固化進可測代碼的體現。"""
import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from adapters import base, taiwanpay  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
