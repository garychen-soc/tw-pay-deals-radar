"""驗證 data/promotions.json 的結構與資料正確性（不需網路，CI 可跑）。

守門重點：排程每天產出的 promotions.json 一旦欄位缺漏、狀態值非法、日期格式錯、
或 url 不在官方白名單，測試就會失敗，避免壞資料上線到網站。
"""
import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "data" / "promotions.json").read_text("utf-8"))
PROVIDERS = json.loads((ROOT / "config" / "providers.json").read_text("utf-8"))

ALLOWED = set()
for _p in PROVIDERS["providers"]:
    ALLOWED.update(_p["official_domains"])
ALLOWED.update(PROVIDERS.get("extra_allowed_domains", []))

LIFECYCLE = {"active", "upcoming", "ended"}
QUOTA = {"not_marked_full", "sold_out", "partial_sold_out", "unknown_app_only", "confirmed_available"}
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def host_ok(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ALLOWED)


class TestPromotions(unittest.TestCase):
    def test_top_level(self):
        self.assertEqual(DATA.get("schema_version"), 1)
        self.assertIn("generated_at", DATA)
        self.assertIsInstance(DATA.get("activities"), list)
        self.assertTrue(DATA["activities"], "activities 不可為空")

    def test_activity_fields(self):
        for a in DATA["activities"]:
            for field in ("id", "provider_name", "title", "url", "lifecycle", "quota_status"):
                self.assertTrue(a.get(field), f"活動缺欄位 {field}: {a.get('id')}")
            self.assertIn(a["lifecycle"], LIFECYCLE, a["id"])
            self.assertIn(a["quota_status"], QUOTA, a["id"])
            self.assertIsInstance(a.get("is_high_return", False), bool)
            for key in ("start_date", "end_date"):
                val = a.get(key)
                if val is not None:
                    self.assertRegex(val, ISO, f"{a['id']} {key} 非 ISO 日期")

    def test_no_ended_activities(self):
        for a in DATA["activities"]:
            self.assertNotEqual(a["lifecycle"], "ended", f"已結束活動不應輸出: {a['id']}")

    def test_end_not_before_start(self):
        for a in DATA["activities"]:
            s, e = a.get("start_date"), a.get("end_date")
            if s and e:
                self.assertLessEqual(s, e, f"{a['id']} 起訖顛倒")

    def test_urls_whitelisted(self):
        for a in DATA["activities"]:
            self.assertTrue(host_ok(a["url"]), f"{a['id']} url 不在官方白名單: {a['url']}")
        for post in DATA.get("ptt_posts", []):
            self.assertTrue(host_ok(post["url"]), f"PTT url 不在白名單: {post['url']}")

    def test_unique_ids(self):
        ids = [a["id"] for a in DATA["activities"]]
        self.assertEqual(len(ids), len(set(ids)), "活動 id 有重複")

    def test_no_known_broken_url_patterns(self):
        """封鎖 2026-07-25 實測會 404／導回首頁的壞連結格式，防止排程再犯。"""
        for a in DATA["activities"]:
            u = a["url"]
            self.assertNotIn(
                "/tpay/news/event/", u,
                f"{a['id']} 台灣Pay 活動頁須用 /fisc-tpay/ 前綴，/tpay/ 會 404：{u}")
            self.assertNotIn(
                "www.pxpayplus.com/activity_content_page", u,
                f"{a['id']} 全支付須用 marketing.pxpayplus.com/pxplus_marketing_page/，www 會導回首頁：{u}")
            self.assertNotRegex(
                u, r"^https?://pluspay\.com\.tw",
                f"{a['id']} 全盈須用 www.pluspay.com.tw（裸網域連不上）：{u}")

    def test_featured_ids_exist(self):
        ids = {a["id"] for a in DATA["activities"]}
        for fid in DATA.get("featured_ids", []):
            self.assertIn(fid, ids, f"featured_id 對應不到活動: {fid}")

    def test_provider_ids_known(self):
        known = {p["id"] for p in PROVIDERS["providers"]}
        for a in DATA["activities"]:
            if a.get("provider_id"):
                self.assertIn(a["provider_id"], known, f"未知 provider_id: {a['provider_id']} ({a['id']})")


if __name__ == "__main__":
    unittest.main()
